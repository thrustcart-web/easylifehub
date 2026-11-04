# EasyLifeHub: SEO + AEO + GEO + Analytics setup

This plan uses standard search fundamentals and does not promise rankings or AI citations.

## Editorial rules for every new article

- The first paragraph answers the main search question directly, before background.
- Use 4-6 specific H2 sections. The first paragraph under each heading answers that section's question.
- Include natural variants and related entities only when they add meaning; do not repeat keywords unnaturally.
- Separate verified product facts from general buying advice. Never invent dimensions, materials, ports, compatibility, warranty, prices, ratings or personal testing.
- Use the three-paragraph introduction, affiliate disclosure, green Amazon CTA, body sections, "Who May Find It Useful?", second CTA and "Final Thoughts" layout.
- Keep affiliate disclosure visible and Amazon links marked nofollow sponsored.
- Do not add fake Product ratings, fake Review schema, or Offers/prices that are not verified and genuinely displayed.
- AI-assisted text must still be reviewed for accuracy, originality and usefulness before publishing.

## Technical SEO on Blogger

1. Keep the blog public and make sure posts are crawlable.
2. In Blogger Settings, enable the blog search description and write a concise, accurate description.
3. Review custom robots.txt and custom robots header tags before changing them. Do not block /search/label, posts, or sitemap URLs accidentally.
4. Use the canonical HTTPS custom domain consistently. Avoid duplicate pages or thin tag archives.
5. In Google Search Console, add the exact HTTPS property for https://www.easylifehub.top/ and verify ownership.
6. Submit the Blogger sitemap at https://www.easylifehub.top/sitemap.xml in Search Console. Check the Sitemaps and Pages/Indexing reports.
7. Use URL Inspection for a small sample of published posts after material template changes.

## Google Analytics 4

Blogger has a native GA4 field. Create or select a GA4 property, create a Web data stream for https://www.easylifehub.top/, copy the Measurement ID beginning with G-, then in Blogger open Settings > Basic > Google Analytics Measurement ID, paste it and Save.

After setup, use GA4 Realtime to confirm a visit is received. Do not paste a second Google tag into the theme if the native Blogger field is already active; duplicate tags can double-count page_view events.

## Link GA4 to Search Console

After both services are set up, link the Search Console property to the GA4 property from GA4 Admin > Product links > Search Console Links. This joins organic search queries/landing-page context with site engagement reports where Google makes the data available.

## What to monitor

- GA4: users, sessions, landing pages, engagement, outbound Amazon clicks, and key events.
- Search Console: clicks, impressions, CTR, average position, indexing exclusions, sitemap status and query/page performance.
- Review every 2-4 weeks. Improve pages with impressions but weak CTR, and expand pages that receive relevant clicks and engagement.
- Track outbound Amazon CTA clicks as a key event only if a click event is actually implemented and tested. Do not label clicks as purchases or affiliate revenue.

## AEO/GEO principles

Answer-engine and generative-engine optimization are not separate markup tricks. Keep answers explicit, facts attributable, entities consistent, sections easy to understand, and pages accessible to crawlers. Structured data can help Google understand eligible page types, but it does not guarantee rich results or AI citations. Avoid mass-producing near-duplicate question pages, artificial mentions, keyword stuffing, or relying on llms.txt as a ranking shortcut.
