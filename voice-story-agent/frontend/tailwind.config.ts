import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/app/**/*.{ts,tsx}",
    "./src/components/**/*.{ts,tsx}",
    "./src/hooks/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        // Soft rounded font suits a children's storybook aesthetic.
        sans: ["var(--font-nunito)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      colors: {
        // Warm pastel palette for the storybook UI.
        story: {
          cream: "#FFF8F0",
          lavender: "#E8D5F5",
          sky: "#D4EEFF",
          mint: "#D4F5E9",
          peach: "#FFE4CC",
        },
      },
      animation: {
        "gentle-pulse": "pulse 2.5s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
    },
  },
  plugins: [],
};

export default config;
