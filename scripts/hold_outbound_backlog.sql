-- Hold the outbound backlog before a worker-outbound cutover.
--
-- Run this on any environment that has been running WITHOUT a worker on the
-- `outbound` queue (see CHANGELOG 2026-08-20). Such an environment has a backlog
-- of PENDING messages and un-ACKed SAP_CONFIRMED POs that were never dispatched.
-- Starting the worker against that backlog fires all of it at the retailer within
-- ~2 minutes -- and from a whitelisted IP those sends succeed.
--
-- Run BEFORE the deploy that starts worker-outbound, not after: the retry sweep
-- runs every 2 minutes and the ACK trigger every 5, so holding afterwards is a race.
--
-- Idempotent. Safe to run twice.
--
--   docker compose exec -T postgres psql -U edi -d <db> -f - < scripts/hold_outbound_backlog.sql
--
-- Release one message when it should genuinely go out:
--   UPDATE edi_outbound_messages SET next_retry_at = now() WHERE id = '<id>';

BEGIN;

-- 1. Hold everything already queued.
--    enqueue_due_retries() filters on `next_retry_at IS NOT NULL AND <= now()`,
--    so NULL already means "not scheduled" -- no new status value needed.
UPDATE edi_outbound_messages
   SET next_retry_at = NULL,
       updated_at    = now()
 WHERE status = 'PENDING'
   AND next_retry_at IS NOT NULL;

-- 2. Pre-create a held ACK for every SAP_CONFIRMED PO that has none.
--    trigger_acks_for_confirmed_pos() creates an ACK *and enqueues it directly*,
--    bypassing the retry sweep entirely -- so the only way to hold one is for the
--    row to already exist. Payload mirrors _build_ack_payload() in
--    app/workflows/b1_to_outbound.py; keep the two in step.
INSERT INTO edi_outbound_messages
       (id, po_id, trading_partner_id, doc_type, external_reference, payload,
        channel, status, attempt_count, next_retry_at, created_at, updated_at)
SELECT gen_random_uuid(),
       o.id,
       p.id,
       'PO_ACK_855'::edi_doc_type_t,
       o.buyer_po_number,
       CASE WHEN p.source_channel = 'EMAIL'::source_channel_t THEN
                 jsonb_build_object(
                     'po_number', o.buyer_po_number,
                     'status',    'PROCESSING',
                     'to',        coalesce(p.api_config ->> 'ops_email', ''),
                     'subject',   'PO Acknowledgement — ' || o.buyer_po_number,
                     'body_text', 'Dear ' || p.name || E'\n\n'
                                  || 'We have received your Purchase Order '
                                  || o.buyer_po_number
                                  || ' and it is currently being processed.'
                                  || E'\n\nThank you,\nLet''s Try Foods')
            ELSE jsonb_build_object(
                     'po_number', o.buyer_po_number,
                     'status',    'PROCESSING')
       END,
       p.source_channel::text,
       'PENDING',
       0,
       NULL,          -- held
       now(),
       now()
  FROM edi_purchase_orders o
  JOIN trading_partners    p ON p.id = o.trading_partner_id
 WHERE o.po_status   = 'SAP_CONFIRMED'::po_status_t
   AND o.deleted_at IS NULL
   AND NOT EXISTS (SELECT 1
                     FROM edi_outbound_messages m
                    WHERE m.po_id    = o.id
                      AND m.doc_type = 'PO_ACK_855'::edi_doc_type_t);

-- 3. Report what is now held. Every row must show next_retry_at = NULL.
SELECT doc_type,
       status,
       count(*)                                        AS rows,
       count(*) FILTER (WHERE next_retry_at IS NOT NULL) AS still_due,
       count(*) FILTER (WHERE attempt_count > 0)         AS already_attempted
  FROM edi_outbound_messages
 GROUP BY doc_type, status
 ORDER BY doc_type, status;

COMMIT;
