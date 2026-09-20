/* The Origami FarmOS brand lockup.
 *
 * Both files are the design package's own SVGs, served from /public rather
 * than inlined: unlike the icon set they never take their colour from the
 * text around them, and the browser can cache one file across every page.
 *
 * Plain <img> on purpose — the console is a static export with no image
 * optimiser behind it, and next/image would only add a wrapper.
 */

/** The folded mark alone, for tight chrome. */
export function BrandMark({ size = 34, className }: { size?: number; className?: string }) {
  return (
    /* eslint-disable-next-line @next/next/no-img-element */
    <img
      src="/brand/origami-farmos-mark.svg"
      alt=""
      width={size}
      height={size}
      className={className}
    />
  );
}

/** Mark plus wordmark, for the entry screens. */
export function BrandLogo({ height = 46, className }: { height?: number; className?: string }) {
  return (
    /* eslint-disable-next-line @next/next/no-img-element */
    <img
      src="/brand/origami-farmos-logo.svg"
      alt="Origami FarmOS"
      height={height}
      style={{ height, width: "auto" }}
      className={className}
    />
  );
}
