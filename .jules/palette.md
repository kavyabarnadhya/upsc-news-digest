## 2025-05-15 - [Accessibility and Contrast in HTML Emails]
**Learning:** WCAG AA compliance (4.5:1) is crucial for readability in emails, especially on mobile. "Specialist" category colors often need manual darkening to remain accessible against white backgrounds. Contextual ARIA labels (like `aria-label="Read full article: {title}"`) solve the "ambiguous link text" problem when multiple "Read more" links exist on one page.
**Action:** Always verify color contrast using a tool (or standard darker hex codes) and use semantic headers (`<h3>`) even when the visual design requires inline styling to mimic spans.

## 2025-05-16 - [Scannability and Assistive Noise Reduction in Digests]
**Learning:** For daily digests, scannability is paramount. Adding article counts to navigation and an estimated reading time significantly reduces the cognitive load for the user. Additionally, while emojis and decorative arrows provide visual delight, they create unnecessary noise for screen readers in an already dense email;  is essential for these elements.
**Action:** Always include "at-a-glance" meta-info (counts, time) in headers and wrap all decorative glyphs in  spans.

## 2025-05-16 - [Scannability and Assistive Noise Reduction in Digests]
**Learning:** For daily digests, scannability is paramount. Adding article counts to navigation and an estimated reading time significantly reduces the cognitive load for the user. Additionally, while emojis and decorative arrows provide visual delight, they create unnecessary noise for screen readers in an already dense email; `aria-hidden="true"` is essential for these elements.
**Action:** Always include "at-a-glance" meta-info (counts, time) in headers and wrap all decorative glyphs in `aria-hidden="true"` spans.

## 2025-05-17 - [Semantic Structure in Email Digests]
**Learning:** Transitioning from generic `<div>` stacks to semantic HTML (`<article>`, `<h3>`, `<ul>`, `<main>`) significantly improves the experience for assistive technology users without impacting visual design. Screen readers can use landmark navigation (`<main>`) and structural navigation (headings and articles) to quickly parse a dense daily digest.
**Action:** Use semantic landmarks and heading levels in email templates, even when visual consistency requires complex inline styling to match previous non-semantic designs.
