/* The Origami brand.
 *
 * The logo is the folded pinwheel — the same artwork the favicon, the
 * installed-app icon and the mobile icon set are cut from, so the console
 * looks like the product in a browser tab, on a home screen and in its own
 * chrome. It carries no wordmark of its own; where a name has to appear
 * beside it, `BrandLogo` sets it in the UI face rather than inventing a
 * second piece of artwork.
 *
 * Served from /public rather than inlined: unlike the icon set it never
 * takes colour from the text around it, and the browser caches one file
 * across every page. Two sizes, so a 40px mark stays crisp on a retina
 * screen without every page paying for the 512.
 *
 * Plain <img> on purpose — the console is a static export with no image
 * optimiser behind it, and next/image would only add a wrapper.
 */

/** The mark alone. */
export function BrandMark({ size = 36, className }: { size?: number; className?: string }) {
  return (
    /* eslint-disable-next-line @next/next/no-img-element */
    <img
      src="/brand/origami-mark.png"
      srcSet="/brand/origami-mark.png 1x, /brand/origami-mark@2x.png 2x"
      alt=""
      width={size}
      height={size}
      style={{ width: size, height: size, display: "block" }}
      className={className}
    />
  );
}

/** Mark plus the product name, for chrome and entry screens. */
export function BrandLogo({ size = 40, className }: { size?: number; className?: string }) {
  return (
    <span className={`brand-lockup${className ? ` ${className}` : ""}`}>
      <BrandMark size={size} />
      <span className="words" style={{ fontSize: Math.round(size * 0.46) }}>
        Origami<span className="alt">FarmOS</span>
      </span>
    </span>
  );
}
