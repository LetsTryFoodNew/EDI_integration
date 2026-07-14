interface Props {
  amount: string | number | null | undefined;
  currency?: string;
  className?: string;
}

const INR_FORMATTER = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export default function MoneyDisplay({ amount, currency = "INR", className }: Props) {
  if (amount === null || amount === undefined || amount === "") {
    return <span className={className}>—</span>;
  }
  const num = typeof amount === "string" ? parseFloat(amount) : amount;
  if (isNaN(num)) return <span className={className}>—</span>;

  const formatted =
    currency === "INR"
      ? INR_FORMATTER.format(num)
      : new Intl.NumberFormat("en-IN", { style: "currency", currency }).format(num);

  return <span className={className}>{formatted}</span>;
}
