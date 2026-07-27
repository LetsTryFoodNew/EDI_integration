interface Props {
  name: string;
  color: string;
  size?: number;
}

export default function CompanyAvatar({ name, color, size = 36 }: Props) {
  return (
    <div
      className="flex items-center justify-center rounded-lg font-bold text-white text-sm flex-shrink-0"
      style={{ width: size, height: size, background: color }}
    >
      {name.slice(0, 2).toUpperCase()}
    </div>
  );
}
