/** Static export: the site needs no server or database. */
const basePath = process.env.PIGTAIL_SITE_BASE_PATH || "";

export default {
  output: "export",
  outputFileTracingRoot: import.meta.dirname,
  basePath,
  trailingSlash: true,
  images: { unoptimized: true },
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
};
