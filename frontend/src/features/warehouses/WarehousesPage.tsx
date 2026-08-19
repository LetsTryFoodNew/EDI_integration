import { Building2, Warehouse } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import BranchesTab from "./components/BranchesTab";
import WarehousesTab from "./components/WarehousesTab";

// Branch Master (SAP OBPL) and Warehouse Master (SAP OWHS) — our own org structure,
// unlike the ship-to / bill-to addresses on the Master Data screen, which are the
// retailer's and carry a mapping decision. There is nothing to map here: SAP owns
// every business field, so the only action is parking a row locally.

export default function WarehousesPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Warehouses</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Our own branches and warehouses, mirrored from SAP. Every field comes from SAP
          except <span className="font-medium">Locally</span> and the note — park a row to
          stop routing POs to it without waiting for a SAP change.
        </p>
      </div>

      <Tabs defaultValue="warehouses">
        <TabsList>
          <TabsTrigger value="warehouses" className="gap-1.5">
            <Warehouse className="h-3.5 w-3.5" />
            Warehouses
          </TabsTrigger>
          <TabsTrigger value="branches" className="gap-1.5">
            <Building2 className="h-3.5 w-3.5" />
            Branches
          </TabsTrigger>
        </TabsList>

        <TabsContent value="warehouses" className="mt-4">
          <WarehousesTab />
        </TabsContent>
        <TabsContent value="branches" className="mt-4">
          <BranchesTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
