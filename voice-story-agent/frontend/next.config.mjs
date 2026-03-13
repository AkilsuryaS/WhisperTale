/** @type {import('next').NextConfig} */
const nextConfig = {
  // Strict mode catches subtle React bugs early in development.
  reactStrictMode: true,

  // Allow image optimisation for any GCS-hosted illustration URLs.
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "storage.googleapis.com",
        pathname: "/**",
      },
    ],
  },
};

export default nextConfig;
