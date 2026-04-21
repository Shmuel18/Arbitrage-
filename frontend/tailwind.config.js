module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          teal: '#2DB8C4',
          'teal-hover': '#24A3AF',
          'teal-deep': '#1A8A94',
          navy: '#1B3A6B',
          'navy-mid': '#2A4D82',
          'navy-deep': '#0E1F3C',
        },
        primary: '#2DB8C4',
        secondary: '#1B3A6B',
      },
    },
  },
  plugins: [],
}
