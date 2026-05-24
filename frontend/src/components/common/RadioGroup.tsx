interface Option {
  value: string;
  label: string;
  desc: string;
}

interface Props {
  name: string;
  options: Option[];
  value: string;
  onChange: (value: string) => void;
}

export default function RadioGroup({ name, options, value, onChange }: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {options.map((opt) => (
        <label
          key={opt.value}
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 10,
            cursor: "pointer",
            padding: "8px 10px",
            borderRadius: "var(--r)",
            background: value === opt.value ? "var(--surface-2)" : "transparent",
            border: `1px solid ${value === opt.value ? "var(--accent)" : "var(--line)"}`,
          }}
        >
          <input
            type="radio"
            name={name}
            value={opt.value}
            checked={value === opt.value}
            onChange={() => onChange(opt.value)}
            style={{ marginTop: 2 }}
          />
          <div>
            <div style={{ fontSize: 13, fontWeight: 500 }}>{opt.label}</div>
            <div style={{ fontSize: 12, color: "var(--fg-mute)", marginTop: 2 }}>{opt.desc}</div>
          </div>
        </label>
      ))}
    </div>
  );
}
