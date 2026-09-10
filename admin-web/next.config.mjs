/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // The console is a pure client-side app — every page is "use client" and
  // reads the API over fetch — so it ships as static files that the API
  // container serves itself. That keeps the whole product one image with
  // one process and no Node runtime in production.
  output: "export",

  // Export writes each route as <route>/index.html, which is what
  // Starlette's StaticFiles(html=True) resolves a directory URL to.
  trailingSlash: true,
};

export default nextConfig;
