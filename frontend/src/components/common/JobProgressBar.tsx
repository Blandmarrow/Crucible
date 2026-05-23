interface Props {
  message: string;
  percent: number;
}

export default function JobProgressBar({ message, percent }: Props) {
  return (
    <div>
      <div style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 4 }}>{message}</div>
      <div style={{ height: 6, borderRadius: 3, background: "var(--surface-3)", overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${percent}%`, background: "var(--accent)", transition: "width 0.3s" }} />
      </div>
    </div>
  );
}
