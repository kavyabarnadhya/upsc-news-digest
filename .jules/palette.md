## 2025-05-15 - [Accessibility and Contrast in HTML Emails]
**Learning:** WCAG AA compliance (4.5:1) is crucial for readability in emails, especially on mobile. "Specialist" category colors often need manual darkening to remain accessible against white backgrounds. Contextual ARIA labels (like `aria-label="Read full article: {title}"`) solve the "ambiguous link text" problem when multiple "Read more" links exist on one page.
**Action:** Always verify color contrast using a tool (or standard darker hex codes) and use semantic headers (`<h3>`) even when the visual design requires inline styling to mimic spans.

## 2025-05-16 - [Scannability and Assistive Noise Reduction in Digests]
**Learning:** For daily digests, scannability is paramount. Adding article counts to navigation and an estimated reading time significantly reduces the cognitive load for the user. Additionally, while emojis and decorative arrows provide visual delight, they create unnecessary noise for screen readers in an already dense email; `aria-hidden="true"` is essential for these elements.
**Action:** Always include "at-a-glance" meta-info (counts, time) in headers and wrap all decorative glyphs in `aria-hidden="true"` spans.

## 2025-05-17 - [Semantic Structure in Email Digests]
**Learning:** Transitioning from generic `<div>` stacks to semantic HTML (`<article>`, `<h3>`, `<ul>`, `<main>`) significantly improves the experience for assistive technology users without impacting visual design. Screen readers can use landmark navigation (`<main>`) and structural navigation (headings and articles) to quickly parse a dense daily digest.
**Action:** Use semantic landmarks and heading levels in email templates, even when visual consistency requires complex inline styling to match previous non-semantic designs.

## 2025-05-18 - [Email-Safe Accessibility Controls]
**Learning:** For email accessibility features like "Skip to Content" links, avoid JavaScript handlers (`onfocus`) as most email clients strip them. A `<style>` block using `:focus` classes is more robust, though still not supported by all clients (e.g., Gmail on mobile). Always provide descriptive `aria-label` attributes for navigational links even if they contain text, as it provides clearer intent for screen reader users (e.g., "Jump to section" vs just "Economy").
**Action:** Prioritize CSS-based focus states over JavaScript for email interactivity and use descriptive ARIA labels to clarify navigational intent.

## 2025-05-19 - [Accessible Contrast for Themed Section Headers]
**Learning:** Themed section headers using background colors with white text must be carefully selected to meet WCAG AA contrast ratios (4.5:1). Darker, more saturated versions of brand colors (e.g., #196f3d for green, #21618c for blue, #a04000 for orange) ensure readability without sacrificing the visual identity of the sections.
**Action:** Always verify contrast ratios for any new color tokens added to the TOPIC_COLORS mapping and prefer darkened shades for backgrounds that host light text.

## 2024-06-07 - [Print-Optimized Styles for Study-Heavy Content]
**Learning:** For study-oriented content like UPSC digests, users often print materials for offline reading and annotation. Standard email templates with sticky headers, navigation bars, and "back to top" links create significant clutter on paper. A @media print stylesheet that hides these elements and uses 'break-inside: avoid' on article cards ensures a professional, readable, and paper-efficient study guide.
**Action:** Always include a print-optimized media query for digests or long-form reports, and use semantic classes to target navigational noise.

## 2026-07-19 - [Robust Programmatic Focus Management and Contrast in HTML Email Jumps]
**Learning:** When using in-page skip-links and anchor links (e.g., jump-to-section) inside HTML emails, screen readers and key-based browsers often fail to redirect focus to non-interactive container targets (such as `<nav>`, `<main>`, and `<section>`). Setting `tabindex="-1"` on these target elements enables robust programmatic focus shifting, while suppressing browser-default visual focus rings with `[tabindex="-1"]:focus { outline: none !important; }` avoids visual clutter. Additionally, focus outlines on interactive components (like `.topic-pill`) must contrast sharply against parent card backgrounds (e.g. using theme colors like `#1a1a2e` in light mode, `#fff` in dark mode) to comply with WCAG 2.4.7.
**Action:** Always use `tabindex="-1"` on target containers of skiplinks/anchor links and customize `:focus-visible` outline colors to contrast explicitly against the actual parent card or background.

## 2026-07-20 - [Non-disruptive Skip-Links and Elevated Contrast in Dark Mode Email Readers]
**Learning:** Skip-to-content links that display inline (`position: static`) upon receiving focus cause massive visual layout shifts that disrupt the reading flow. Centering focused links absolutely (`position: absolute; left: 50%; top: 10px; transform: translateX(-50%);`) solves this layout shift while keeping the focus element highly visible. Furthermore, default dark mode focus indicator outlines (like `#1a1a2e` on color categories) suffer from zero contrast on dark canvases; explicit white outlines (`#fff`) and elevated box-shadow highlights for container hovers/focuses are essential to preserve accessibility.
**Action:** Design skip-links to overlay absolutely to prevent layout shifts, and override interactive focus styles with white outlines in prefers-color-scheme dark rules.

## 2026-07-21 - [Print Optimization and Link Disclosure for Offline Study]
**Learning:** For study-heavy content, users frequently print materials to study offline. Appending target URLs next to active anchor links (e.g., using `a::after { content: " (" attr(href) ")"; }`) ensures that printed copies retain the contextual resource information, while hiding interactive actions like "Read full article" reduces visual clutter and paper consumption.
**Action:** Always include a print media query that hides interactive elements and dynamically reveals destination links beside the text anchors.

## 2026-08-09 - [Accessible Stretched Links on Card Layouts]
**Learning:** When implementing stretched links (making an entire card clickable using a pseudo-element `::after` on a nested anchor tag) for high touch-target accessibility (WCAG Target Size), sibling interactive elements and text content can be buried under the absolute overlay if not properly structured. Giving elements like paragraph text, tags, and detail links `position: relative` and a higher `z-index` restores their separate accessibility and text-selectability. Additionally, any print-specific layouts must override/reset these pseudo-elements to prevent overlay interference during hard-copy rendering.
**Action:** Always use relative positioning with `z-index: 2` on text contents and secondary links inside a stretched-link container, and explicitly disable or reset the `::after` overlay within the `@media print` query.

## 2026-08-16 - [Card Hover Feedback & Preserved Link Accessibility in HTML Email]
**Learning:** In HTML email templates, while CSS stretched-link pseudo-elements (`::after`) provide large touch targets in supporting webmail clients, `.read-more` links must remain fully focusable and accessible with descriptive `aria-label` attributes and `aria-hidden="true"` arrow spans due to inconsistent CSS support across desktop/mobile email clients. Adding `.article-card:hover .read-more, .article-card:focus-within .read-more` selectors ensures delightful visual feedback (`translateX(4px)` and brightness boost) whenever the parent article card is hovered or focused via keyboard navigation.
**Action:** Always maintain full link accessibility (`aria-label` + `aria-hidden="true"` on decorative glyphs) on secondary CTA links in email digests, and trigger CTA hover transforms on parent card hover/focus-within states.

## 2026-08-20 - [Accessible External Link Indicators in HTML Email Digests]
**Learning:** External news links in email digests opening in a new tab (`target="_blank" rel="noopener noreferrer"`) prevent users from losing their context within the digest. However, to comply with WCAG guidance, screen reader users must be notified of this behavior. Including `(opens in new tab)` in descriptive `aria-label` attributes ensures screen readers announce tab behavior clearly without adding visual clutter to email cards.
**Action:** Always include `target="_blank" rel="noopener noreferrer"` for external news links and explicitly append `(opens in new tab)` to `aria-label` attributes on link CTAs.

## 2026-09-20 - [Contextual ARIA Navigation & Tactile Active States in HTML Email]
**Learning:** Having multiple links with identical link text (such as repetitive "Back to topics" links throughout a document) fails WCAG 2.4.4 / 2.4.9 Link Purpose requirements when screen reader users navigate via link lists or rotor menus. Providing contextual ARIA labels (e.g. `aria-label="Back to topic index from {safe_topic_name}"`) uniquely identifies the origin section for assistive technology without altering the clean visual text anchor. Additionally, combining focus elevation (`box-shadow`) with `:active` press transforms (`transform: translateY(0) scale(0.98)`) provides tactile feedback for mouse, touch, and keyboard interactions.
**Action:** Dynamically contextualize repetitive link labels using section names, and pair focus elevation with `:active` visual press states on interactive navigation controls.
