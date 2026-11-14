/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Geist", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["Geist Mono", "ui-monospace", "monospace"],
      },
      colors: {
        bg: "var(--bg)",
        surface: {
          DEFAULT: "var(--surface-1)",
          2: "var(--surface-2)",
          3: "var(--surface-3)",
          card: "var(--surface-2)",
          hover: "var(--surface-3)",
        },
        line: {
          DEFAULT: "var(--line)",
          2: "var(--line-2)",
        },
        fg: {
          DEFAULT: "var(--fg)",
          mute: "var(--fg-mute)",
          dim: "var(--fg-dim)",
          soft: "var(--fg-soft)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          2: "var(--accent-2)",
          deep: "var(--accent-deep)",
          glow: "var(--accent-glow)",
          hover: "var(--accent-2)",
          // Green as text (`text-accent-text`): --accent is a fill colour and is
          // too light to read as text in light mode.
          text: "var(--accent-text)",
        },
        "on-accent": "var(--on-accent)",
        good: { DEFAULT: "var(--good)", bg: "var(--good-bg)" },
        warn: { DEFAULT: "var(--warn)", bg: "var(--warn-bg)" },
        bad: { DEFAULT: "var(--bad)", bg: "var(--bad-bg)" },
        info: { DEFAULT: "var(--info)", bg: "var(--info-bg)" },
      },
      borderRadius: {
        sm: "var(--r-sm)",
        DEFAULT: "var(--r)",
        lg: "var(--r-lg)",
        xl: "var(--r-xl)",
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        lg: "var(--shadow-lg)",
      },
    },
  },
  plugins: [],
};
