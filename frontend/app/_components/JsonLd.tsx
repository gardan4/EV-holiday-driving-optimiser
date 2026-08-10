/**
 * Emits a schema.org JSON-LD block.
 *
 * `application/ld+json` is data, not code — browsers never execute it, so this
 * is not the `dangerouslySetInnerHTML` that CSP is worried about. The `<` escape
 * is the one that matters: an unescaped `</script>` inside a string value would
 * close the tag early and drop the rest of the block into the document.
 */
export default function JsonLd({ data }: { data: object }) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{
        __html: JSON.stringify(data).replace(/</g, "\\u003c"),
      }}
    />
  )
}
