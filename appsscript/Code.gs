/**
 * ============================================================================
 *  AutoBlog Publisher  -  the "set up and forget" engine
 * ============================================================================
 *  Runs on Google Apps Script (100% free, no server, no credit card).
 *  Every hour it wakes up, checks whether the current hour is a posting hour,
 *  and if so publishes the next article from the GitHub queue to Blogger.
 *
 *  WHY THIS DESIGN
 *  - Hourly trigger + hour matching  => exact posting times, no drifting cron
 *  - Random sleep inside the hour    => posts never land on the same second,
 *                                       which is what spam classifiers look for
 *  - Circuit breaker                 => after 3 consecutive failures the script
 *                                       stops for 24h and emails you, instead
 *                                       of hammering the Blogger API and getting
 *                                       your account's API access restricted
 *  - Reads content from GitHub raw   => you can edit/queue articles anywhere
 *
 *  SETUP (once, ~10 minutes) - see docs/SETUP_AR.md
 *  1. script.google.com -> New project -> paste this file -> Save
 *  2. Run `setup` and fill in the values it prints in the Execution log
 *     (or edit the DEFAULTS object below, then run `setup` once)
 *  3. Run `installTrigger` once  -> creates the hourly trigger
 *  4. Run `testConnection`        -> must print OK + your blog name
 * ============================================================================
 */

// ---------------------------------------------------------------- defaults --
var DEFAULTS = {
  RAW_BASE:      'https://raw.githubusercontent.com/YOUR_GITHUB_USER/YOUR_REPO/main',
  QUEUE_DIR:     'content/queue',
  BLOG_ID:       'PUT_YOUR_BLOGGER_BLOG_ID_HERE',
  SA_JSON:       '',                 // full contents of the service-account .json
  POST_HOURS:    '9,11,13,15,17,19',          // local hours, comma separated
  MAX_PER_DAY:   '6',
  JITTER_MAX:    '240',              // random 0-4 min delay; Utilities.sleep is capped at 5 min
  NOTIFY_EMAIL:  '',                 // blank = your Google account email
  TZ:            'Africa/Cairo',
  MIN_GAP_HOURS: '2',
  GITHUB_REPO: 'thrustcart-web/easylifehub',
  REFILL_MIN_PENDING: '12',
  REFILL_COUNT: '6',
  REFILL_COOLDOWN_HOURS: '12',
  AUTOMATION_ENABLED: 'false',   // keep scheduled publishing/refill off until explicitly approved
  // ---- category hub + related posts (topical authority plumbing) ----
  BLOG_URL:        '',                    // current custom domain is refreshed from Blogger API
  CATEGORY_HUB:    'content/categories/index.html',
  CATEGORY_PAGE:   'Browse All Categories',
  RELATED_LIMIT:   '4',
  RELATED_ENABLED: 'false'
};

var P = PropertiesService.getScriptProperties();

function setup() {
  Object.keys(DEFAULTS).forEach(function (k) {
    if (!P.getProperty(k)) P.setProperty(k, String(DEFAULTS[k]));
  });
  Logger.log('=== Current settings (edit them in Project Settings > Script Properties) ===');
  Object.keys(DEFAULTS).forEach(function (k) {
    var v = P.getProperty(k) || '';
    Logger.log(k + ' = ' + (k === 'SA_JSON' ? (v ? '[' + v.length + ' chars]' : '(EMPTY)') : v));
  });
  Logger.log('');
  Logger.log('Next: run testConnection(), then createQueuePreviewDraft() for a Blogger draft.');
  Logger.log('Scheduled publishing/refill remain DISABLED until AUTOMATION_ENABLED is explicitly set to true.');
}

function cfg(key, fallback) {
  var v = P.getProperty(key);
  return (v === null || v === '') ? (fallback || DEFAULTS[key] || '') : v;
}

// Remove the obsolete manual spending toggle. SerpApi is the primary
// product source; Apify is a separately guarded fallback and stock-audit source.
function removeLegacyPaidLookupToggle_() {
  if (P.getProperty('PAID_LOOKUP_ENABLED') !== null) {
    P.deleteProperty('PAID_LOOKUP_ENABLED');
    Logger.log('Removed obsolete PAID_LOOKUP_ENABLED property; product lookup is automatic on low-queue refill.');
  }
}

// --------------------------------------------------------- service account --
function getAccessToken_() {
  // Use the Google account that owns/runs this Apps Script project.
  // Blogger write access is tied to the signed-in account, not a public
  // service-account identity. The manifest must include the Blogger scope.
  var token = ScriptApp.getOAuthToken();
  if (!token) {
    throw new Error('No Apps Script OAuth token. Run createQueuePreviewDraft() and approve the requested Blogger permissions.');
  }
  return token;
}

function b64url_(str) {
  return Utilities.base64EncodeWebSafe(str).replace(/=+$/, '');
}
function b64urlBytes_(bytes) {
  // computeRsaSha256Signature returns a byte array
  var blob = Utilities.newBlob(bytes);
  return Utilities.base64EncodeWebSafe(blob.getBytes()).replace(/=+$/, '');
}

function bloggerCall_(method, path, payload, params) {
  var url = 'https://blogger.googleapis.com/v3' + path;
  if (params) {
    var qs = Object.keys(params).map(function (k) {
      return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
    }).join('&');
    url += '?' + qs;
  }
  var options = {
    method: method,
    headers: { Authorization: 'Bearer ' + getAccessToken_() },
    muteHttpExceptions: true
  };
  if (payload) {
    options.contentType = 'application/json';
    options.payload = JSON.stringify(payload);
  }
  var res = UrlFetchApp.fetch(url, options);
  var code = res.getResponseCode();
  var text = res.getContentText();
  if (code < 200 || code >= 300) {
    if (code === 403) {
      throw new Error(
        'Blogger API 403: the Google account executing this Apps Script does not have permission to create or edit posts. ' +
        'This version uses the signed-in Apps Script account (not the old service account). ' +
        'Confirm that this same Google account owns the blog or is invited as an Author/Admin, accept the Blogger invitation, ' +
        'then run createQueuePreviewDraft() again. Google response: ' + text.slice(0, 300)
      );
    }
    throw new Error('Blogger API ' + code + ': ' + text.slice(0, 400));
  }
  return text ? JSON.parse(text) : {};
}

var AMAZON_ASSOCIATE_DISCLOSURE_ = 'As an Amazon Associate I earn from qualifying purchases.';
var AFFILIATE_LINK_DISCLOSURE_ = 'Disclosure: This article contains affiliate links. We may earn a commission if you purchase through these links, at no additional cost to you.';

function affiliateLinkDisclosureHtml_() {
  return '<p class="disclosure affiliate-link-disclosure" role="note" ' +
    'style="font-size:13px;line-height:1.5;color:#4a525e;background-color:#f6f8fa;' +
    'border-left:3px solid #2e8b27;padding:8px 11px;margin:18px 0 8px;">' +
    AFFILIATE_LINK_DISCLOSURE_ + '</p>';
}

/**
 * Makes the required Amazon identification statement visible and adds a
 * link-level disclosure immediately before every renderer-owned Amazon CTA.
 * Safe to run repeatedly: an existing disclosure is not duplicated.
 */
function ensureAffiliateDisclosures_(html) {
  var value = String(html || '');
  var associateFound = false;

  value = value.replace(/<p\b[^>]*>[\s\S]*?<\/p\s*>/gi, function(block) {
    var plain = block.replace(/<[^>]+>/g, ' ').replace(/&nbsp;|&#160;/gi, ' ')
      .replace(/&amp;/gi, '&').toLowerCase();
    if (plain.indexOf('as an amazon associate') !== -1) {
      associateFound = true;
      return '<p><em>' + AMAZON_ASSOCIATE_DISCLOSURE_ + '</em></p>';
    }
    return block;
  });
  if (!associateFound) {
    value = '<p><em>' + AMAZON_ASSOCIATE_DISCLOSURE_ + '</em></p>\n' + value;
  }

  var groupCount = 0;
  value = value.replace(
    /<div\b(?=[^>]*\bclass\s*=\s*["'][^"']*\bamazon-cta-group\b[^"']*["'])[^>]*>/gi,
    function(tag, offset, full) {
      groupCount++;
      var before = full.slice(Math.max(0, offset - 700), offset);
      var alreadyAdjacent = /<p\b(?=[^>]*\bclass\s*=\s*["'][^"']*\baffiliate-link-disclosure\b[^"']*["'])[^>]*>[\s\S]*?<\/p\s*>\s*$/i.test(before);
      return alreadyAdjacent ? tag : affiliateLinkDisclosureHtml_() + tag;
    }
  );

  // Older layouts may not wrap buttons in amazon-cta-group. In that case,
  // put a disclosure immediately before each direct Amazon product anchor.
  if (groupCount === 0) {
    value = value.replace(
      /<a\b(?=[^>]*\bhref\s*=\s*["']https?:\/\/(?:www\.)?amazon\.com\/dp\/)[^>]*>/gi,
      function(tag, offset, full) {
        var before = full.slice(Math.max(0, offset - 700), offset);
        var alreadyAdjacent = /<p\b(?=[^>]*\bclass\s*=\s*["'][^"']*\baffiliate-link-disclosure\b[^"']*["'])[^>]*>[\s\S]*?<\/p\s*>\s*$/i.test(before);
        return alreadyAdjacent ? tag : affiliateLinkDisclosureHtml_() + tag;
      }
    );
  }
  return value;
}

function insertPost_(title, html, labels) {
  return bloggerCall_('POST', '/blogs/' + cfg('BLOG_ID') + '/posts',
    { title: title, content: ensureAffiliateDisclosures_(html), labels: labels || [] });
}

function updatePost_(postId, title, html, labels) {
  return bloggerCall_('PUT', '/blogs/' + cfg('BLOG_ID') + '/posts/' + postId,
    { title: title, content: ensureAffiliateDisclosures_(html), labels: labels || [] });
}

// ------------------------------------------------- links, related, hub page --
/** The blog's current address. Refresh from Blogger so a custom-domain change cannot leave stale links cached. */
function blogUrl_() {
  var cached = String(cfg('BLOG_URL') || '').replace(/\/+$/, '');
  try {
    var blog = bloggerCall_('GET', '/blogs/' + cfg('BLOG_ID'));
    var detected = String(blog.url || '').replace(/\/+$/, '');
    if (detected) {
      if (detected !== cached) P.setProperty('BLOG_URL', detected);
      return detected;
    }
  } catch (e) {
    Logger.log('blogUrl_ API refresh: ' + e);
  }
  return cached;
}

/**
 * The Python side writes relative links (/search/label/Kitchen, /p/categories.html)
 * because it cannot know the final blog address. This makes them absolute.
 * Blogger resolves relative hrefs against the dashboard, not the blog, so
 * skipping this step would produce dead links in every post.
 */
function absolutizeLinks_(html) {
  var base = blogUrl_();
  if (!base) return html;
  return String(html)
    .replace(/href="\/search\/label\//g, 'href="' + base + '/search/label/')
    .replace(/href="\/p\/categories\.html"/g, 'href="' + base + '/p/categories.html"');
}


/**
 * Final content guard for every Blogger post.
 * Remove embedded CSS/JS and unwrap all non-Amazon links.
 */
function sanitizePostHtml_(html) {
  var value = String(html || '');
  value = value.replace(/<style\b[^>]*>[\s\S]*?<\/style\s*>/gi, '');
  value = value.replace(/<script\b[^>]*>[\s\S]*?<\/script\s*>/gi, '');

  var firstTag = value.search(/<[a-z!/][^>]*>/i);
  if (firstTag > 0) {
    var prefix = value.slice(0, firstTag);
    if (/[{}]/.test(prefix) && /(?:^|})\s*[.#@][a-z_-]/i.test(prefix)) {
      value = value.slice(firstTag);
    }
  }

  value = value.replace(/<a\b([^>]*)>([\s\S]*?)<\/a\s*>/gi, function(full, attrs, inner) {
    var match = String(attrs || '').match(/\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);
    if (!match) return inner || '';

    var href = String(match[1] || match[2] || match[3] || '')
      .replace(/&amp;/gi, '&')
      .replace(/&#0*38;/gi, '&')
      .replace(/&quot;/gi, '"')
      .trim();

    var hostMatch = href.match(/^https?:\/\/([^\/?#:]+)(?::\d+)?/i);
    var host = hostMatch ? hostMatch[1].toLowerCase() : '';
    if (host === 'amazon.com' || /\.amazon\.com$/.test(host) || host === 'amzn.to' || host === 'amzn.com') return full;
    return inner || '';
  });

  return value.trim();
}

/**
 * Fill the empty <div class="related"> placeholder with live posts that share
 * this article's label. Done after insert (not before) so the article never
 * links to itself, and via PUT so the published body carries the links Google
 * needs to see the internal-link graph.
 */
function appendRelated_(post, meta, labels) {
  Logger.log('Related-post links are disabled by the fixed article layout.');
  return null;
}

/**
 * Publish (or refresh) the "Browse all categories" page.
 * It is a Blogger PAGE, not a post: pages never count toward the posting rate,
 * so this cannot contribute to a spam-pattern signal.
 */
function syncCategoryPage() {
  Logger.log('Category hub page creation is disabled.');
  return null;
}

/**
 * One-time cleanup for legacy pages created by the old category-hub workflow.
 * Deletes only the exact generated title and completely empty untitled pages.
 */
function cleanupGeneratedCategoryPages() {
  var blogId = cfg('BLOG_ID');
  var pages = (bloggerCall_('GET', '/blogs/' + blogId + '/pages',
    { maxResults: 150 }).items) || [];
  var deleted = 0;
  pages.forEach(function(page) {
    var title = String(page.title || '').trim().toLowerCase();
    var content = String(page.content || '').replace(/<[^>]*>/g, '').trim();
    var isGeneratedHub = title === 'browse all categories';
    var isEmptyUntitled = !title && !content;
    if (!page.id || (!isGeneratedHub && !isEmptyUntitled)) return;
    bloggerCall_('DELETE', '/blogs/' + blogId + '/pages/' + page.id);
    deleted++;
    Logger.log('Deleted legacy generated page: ' + (page.title || '(empty page)'));
  });
  Logger.log('Legacy category/empty page cleanup complete. Deleted=' + deleted);
  return deleted;
}

// ------------------------------------------------------------- queue state --
function getPublished_() {
  var raw = P.getProperty('PUBLISHED') || '[]';
  try { return JSON.parse(raw); } catch (e) { return []; }
}
function setPublished_(arr) {
  P.setProperty('PUBLISHED', JSON.stringify(arr.slice(-500)));
}
function isPublished_(slug) {
  return getPublished_().some(function (p) { return p.slug === slug; });
}

function isDraftedSlug_(slug) {
  return parseJsonArrayProperty_('BLOG_DRAFT_SLUGS').some(function (value) {
    return String(value) === String(slug);
  });
}

function reserveDraftSlug_(slug) {
  if (!slug) return;
  var list = parseJsonArrayProperty_('BLOG_DRAFT_SLUGS').map(String);
  if (list.indexOf(String(slug)) === -1) list.push(String(slug));
  P.setProperty('BLOG_DRAFT_SLUGS', JSON.stringify(list.slice(-500)));
}

/** True only when every item has a verified, tracked Amazon product detail page. */
var AMAZON_STOCK_MAX_AGE_SECONDS = 24 * 60 * 60;
var AMAZON_STOCK_ALLOWED = { IN_STOCK: true, INSTOCK: true, INSTOCKSCARCE: true };

function productItemStockReady_(item) {
  var source = String(item && item.stock_source || '').toLowerCase();
  if (!item || (source !== 'serpapi' && source !== 'apify')) return false;
  var status = String(item.availability_type || '').toUpperCase();
  var checkedAt = Number(item.stock_checked_at || 0);
  var age = Math.floor(Date.now() / 1000) - checkedAt;
  return !!AMAZON_STOCK_ALLOWED[status] &&
    checkedAt > 0 &&
    age >= 0 &&
    age <= AMAZON_STOCK_MAX_AGE_SECONDS;
}

/** A product link is eligible only with a fresh SerpApi or Apify stock check. */
function productLinksReady_(meta) {
  if (!meta || meta.product_links_verified !== true) return false;
  var items = meta.items || [];
  if (!items.length) return false;
  var seenAsins = {};
  var seenParents = {};
  return items.every(function (item) {
    if (!item ||
        item.asin_verified !== true ||
        !/^[A-Z0-9]{10}$/.test(String(item.asin || '')) ||
        !productItemStockReady_(item) ||
        String(item.url || '').indexOf('https://www.amazon.com/dp/' + item.asin + '?tag=') !== 0) {
      return false;
    }
    var asin = String(item.asin).toUpperCase();
    var parent = String(item.parent_asin || asin).toUpperCase();
    if (seenAsins[asin] || seenParents[parent]) return false;
    seenAsins[asin] = true;
    seenParents[parent] = true;
    return true;
  });
}

/**
 * Require the new CTA-only layout before previewing or publishing.
 * Each verified Amazon product must have exactly two renderer-owned green
 * buttons (mid-article and final CTA). All other anchors and naked URLs fail.
 */
function amazonButtonLayoutReady_(html, meta) {
  var value = String(html || '');
  var items = (meta && meta.items) || [];
  if (!items.length) return false;
  if (/table\s+of\s+contents|class=["'][^"']*\btoc\b|aria-label=["']table of contents/i.test(value)) return false;

  var withoutAnchors = value.replace(/<a\b[^>]*>[\s\S]*?<\/a\s*>/gi, '');
  if (/(?:https?:\/\/|www\.)\S+/i.test(withoutAnchors)) return false;

  var expected = {};
  items.forEach(function (item) {
    var url = String(item && item.url || '').replace(/&amp;/gi, '&').trim();
    if (!url || !/^https:\/\/www\.amazon\.com\/dp\/[A-Z0-9]{10}\?tag=[^&\s]+$/i.test(url)) return;
    expected[url] = 0;
  });
  if (Object.keys(expected).length !== items.length) return false;

  var anchors = value.match(/<a\b[^>]*>/gi) || [];
  if (anchors.length !== items.length * 2) return false;
  for (var i = 0; i < anchors.length; i++) {
    var attrs = anchors[i];
    var hrefMatch = attrs.match(/\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);
    var classMatch = attrs.match(/\bclass\s*=\s*["']([^"']*)["']/i);
    var ownerMatch = attrs.match(/\bdata-owner\s*=\s*["']renderer["']/i);
    if (!hrefMatch || !classMatch || !ownerMatch) return false;
    var classes = String(classMatch[1] || '').split(/\s+/);
    if (classes.indexOf('amazon-cta') === -1) return false;
    var href = String(hrefMatch[1] || hrefMatch[2] || hrefMatch[3] || '')
      .replace(/&amp;/gi, '&').trim();
    if (!Object.prototype.hasOwnProperty.call(expected, href)) return false;
    expected[href]++;
  }
  return items.every(function (item) {
    var url = String(item.url || '').replace(/&amp;/gi, '&').trim();
    return expected[url] === 2;
  });
}
function countPublishedToday_() {
  var today = Utilities.formatDate(new Date(), cfg('TZ'), 'yyyy-MM-dd');
  return getPublished_().filter(function (p) { return (p.ts || '').indexOf(today) === 0; }).length;
}
function lastPublishTime_() {
  var list = getPublished_();
  return list.length ? new Date(list[list.length - 1].ts).getTime() : 0;
}

function parseJsonArrayProperty_(name) {
  try {
    var value = JSON.parse(P.getProperty(name) || '[]');
    return Array.isArray(value) ? value : [];
  } catch (e) {
    Logger.log('Invalid JSON in Script Property ' + name);
    return [];
  }
}

/** Build the ASIN/parent-ASIN reservation set for products already on the blog. */
function reservedProductSets_(queuePosts) {
  var usedAsins = {};
  var usedParents = {};
  parseJsonArrayProperty_('BLOG_EXTERNAL_ASINS').forEach(function (asin) {
    asin = String(asin || '').toUpperCase();
    if (/^[A-Z0-9]{10}$/.test(asin)) usedAsins[asin] = true;
  });
  parseJsonArrayProperty_('BLOG_EXTERNAL_PARENT_ASINS').forEach(function (asin) {
    asin = String(asin || '').toUpperCase();
    if (/^[A-Z0-9]{10}$/.test(asin)) usedParents[asin] = true;
  });

  var reservedSlugs = {};
  parseJsonArrayProperty_('BLOG_RESERVED_SLUGS').forEach(function (slug) {
    reservedSlugs[String(slug)] = true;
  });
  getPublished_().forEach(function (post) {
    if (post && post.slug) reservedSlugs[String(post.slug)] = true;
  });
  (queuePosts || []).forEach(function (meta) {
    if (!meta || !reservedSlugs[String(meta.slug || '')]) return;
    (meta.items || []).forEach(function (item) {
      var asin = String(item && item.asin || '').toUpperCase();
      var parent = String(item && item.parent_asin || asin).toUpperCase();
      if (/^[A-Z0-9]{10}$/.test(asin)) usedAsins[asin] = true;
      if (/^[A-Z0-9]{10}$/.test(parent)) usedParents[parent] = true;
    });
  });
  return { asins: usedAsins, parents: usedParents };
}

function articleUsesReservedProducts_(meta, reserved) {
  var localAsins = {};
  var localParents = {};
  var items = (meta && meta.items) || [];
  for (var i = 0; i < items.length; i++) {
    var item = items[i] || {};
    var asin = String(item.asin || '').toUpperCase();
    var parent = String(item.parent_asin || asin).toUpperCase();
    if (!asin || !parent) return true;
    if (reserved.asins[asin] || reserved.parents[parent] ||
        localAsins[asin] || localParents[parent]) return true;
    localAsins[asin] = true;
    localParents[parent] = true;
  }
  return false;
}

function reserveArticleProducts_(meta, reserved) {
  (meta.items || []).forEach(function (item) {
    var asin = String(item && item.asin || '').toUpperCase();
    var parent = String(item && item.parent_asin || asin).toUpperCase();
    if (/^[A-Z0-9]{10}$/.test(asin)) reserved.asins[asin] = true;
    if (/^[A-Z0-9]{10}$/.test(parent)) reserved.parents[parent] = true;
  });
}

// ---------------------------------------------------------- circuit breaker --
function breakerOpen_() {
  var until = Number(P.getProperty('BREAKER_UNTIL') || 0);
  if (until && new Date().getTime() < until) {
    var mins = Math.round((until - new Date().getTime()) / 60000);
    Logger.log('Circuit breaker OPEN - ' + mins + ' min remaining. Last error: ' + (P.getProperty('LAST_ERROR') || ''));
    return true;
  }
  return false;
}
function recordFailure_(err) {
  var n = Number(P.getProperty('FAIL_COUNT') || 0) + 1;
  P.setProperty('FAIL_COUNT', String(n));
  P.setProperty('LAST_ERROR', String(err).slice(0, 400));
  if (n >= 3) {
    P.setProperty('BREAKER_UNTIL', String(new Date().getTime() + 24 * 3600 * 1000));
    notify_('AutoBlog stopped itself',
      'After 3 consecutive failures the publisher paused for 24 hours to protect your Blogger account.\n\n' +
      'Last error:\n' + String(err).slice(0, 800) + '\n\n' +
      'Fix the cause, then clear FAIL_COUNT / BREAKER_UNTIL in Script Properties (or run resetBreaker()).');
  }
}
function recordSuccess_() {
  P.setProperty('FAIL_COUNT', '0');
  P.deleteProperty('BREAKER_UNTIL');
  P.deleteProperty('LAST_ERROR');
}
function resetBreaker() {
  P.setProperty('FAIL_COUNT', '0');
  P.deleteProperty('BREAKER_UNTIL');
  P.deleteProperty('LAST_ERROR');
  Logger.log('Breaker reset.');
}

// ------------------------------------------------------------------- notify --
function notify_(subject, body) {
  var to = String(cfg('NOTIFY_EMAIL') || '').trim();
  if (!to) {
    Logger.log('Notification skipped: set NOTIFY_EMAIL in Script Properties to enable email alerts.');
    return;
  }
  try {
    MailApp.sendEmail(to, '[AutoBlog] ' + subject, body);
  } catch (e) {
    Logger.log('notify failed: ' + e);
  }
}

// ------------------------------------------------------------------ fetching --
function fetchJson_(url) {
  var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) throw new Error('HTTP ' + res.getResponseCode() + ' for ' + url);
  return JSON.parse(res.getContentText());
}
function fetchText_(url) {
  var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) throw new Error('HTTP ' + res.getResponseCode() + ' for ' + url);
  return res.getContentText();
}

// ==================================================== THE MAIN ENTRY POINT ===
/**
 * Triggered every hour. Publishes when the hour matches POST_HOURS.
 */
function publishScheduled() {
  if (String(cfg('AUTOMATION_ENABLED', 'false')).toLowerCase() !== 'true') {
    Logger.log('Scheduled publishing is paused: AUTOMATION_ENABLED is not true.');
    return;
  }
  var tz = cfg('TZ');
  var now = new Date();
  var hour = Number(Utilities.formatDate(now, tz, 'H'));
  var hours = String(cfg('POST_HOURS')).split(',').map(function (h) { return Number(String(h).trim()); });

  Logger.log(Utilities.formatDate(now, tz, 'yyyy-MM-dd HH:mm') + ' ' + tz + ' | posting hours: ' + hours.join(','));

  if (hours.indexOf(hour) === -1) { Logger.log('Not a posting hour - exiting.'); return; }
  if (breakerOpen_()) return;

  // random delay inside the hour -> no machine-perfect timestamps
  var jitterMax = Math.min(Number(cfg('JITTER_MAX') || 240), 240);
  var jitter = Math.floor(Math.random() * jitterMax);
  Logger.log('Humanising delay: sleeping ' + jitter + 's');
  Utilities.sleep(jitter * 1000);

  try {
    publishNext_(1);
  } catch (err) {
    recordFailure_(err);
    Logger.log('FAILED: ' + err);
    notify_('publish failed', String(err).slice(0, 800));
  }
}

function publishNext_(howMany) {
  var maxPerDay = Number(cfg('MAX_PER_DAY') || 3);
  var alreadyToday = countPublishedToday_();
  if (alreadyToday >= maxPerDay) { Logger.log('Daily cap reached (' + alreadyToday + '/' + maxPerDay + ').'); return 0; }

  var minGap = Number(cfg('MIN_GAP_HOURS') || 2) * 3600 * 1000;
  if (lastPublishTime_() && (new Date().getTime() - lastPublishTime_()) < minGap) {
    Logger.log('Too soon after the last post - skipping.');
    return 0;
  }

  var index = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/index.json');
  var posts = index.posts || [];
  Logger.log('Queue size: ' + posts.length);

  var reserved = reservedProductSets_(posts);
  // Fail closed on any existing article collision, including Blogger drafts.
  var existingBloggerPosts = listBlogPostsByStatus_('LIVE')
    .concat(listBlogPostsByStatus_('SCHEDULED'))
    .concat(listBlogPostsByStatus_('DRAFT'));
  var existingTitleKeys = {};
  existingBloggerPosts.forEach(function (post) {
    var key = normalizedArticleTitleKey_(post.title || '');
    if (key) existingTitleKeys[key] = true;
  });
  var done = 0;
  for (var i = 0; i < posts.length && done < howMany; i++) {
    var meta = posts[i];
    if (!meta || !meta.slug || isPublished_(meta.slug)) continue;
    if (isDraftedSlug_(meta.slug)) {
      Logger.log('Skipping article: it already exists as a Blogger draft: ' + meta.slug);
      continue;
    }
    if (meta.review_required === true) {
      Logger.log('Skipping article pending editorial review: ' + meta.slug);
      continue;
    }
    if (!productLinksReady_(meta)) {
      Logger.log('Skipping article: fresh Amazon in-stock verification is missing or expired: ' + meta.slug);
      continue;
    }
    if (articleUsesReservedProducts_(meta, reserved)) {
      Logger.log('Skipping article: one or more Amazon products are already used by another live/scheduled/published article: ' + meta.slug);
      continue;
    }
    if (Number(meta.layout_version || 0) < 4) {
      Logger.log('Skipping legacy article layout (v4 required): ' + meta.slug);
      continue;
    }
    if (countPublishedToday_() >= maxPerDay) break;

    var html = fetchText_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/' + meta.html_file);
    if (!amazonButtonLayoutReady_(html, meta)) {
      Logger.log('Skipping article: Amazon CTA button layout is missing or contains other links: ' + meta.slug);
      continue;
    }
    var titleKey = normalizedArticleTitleKey_(meta.title || '');
    if ((titleKey && existingTitleKeys[titleKey]) ||
        isDuplicateArticleContent_(html, meta.title, existingBloggerPosts)) {
      Logger.log('Skipping duplicate article: matching title/body already exists in Blogger: ' + meta.title);
      continue;
    }
    var labels = (meta.labels || []).filter(Boolean);

    html = sanitizePostHtml_(html);
    var post = insertPost_(meta.title, html, labels);
    // bonuses - never allowed to break the publish itself

    var published = getPublished_();
    published.push({
      slug: meta.slug,
      post_id: post.id,
      url: post.url,
      title: meta.title,
      niche: meta.niche || '',
      category: meta.category_label || '',
      ts: Utilities.formatDate(new Date(), cfg('TZ'), 'yyyy-MM-dd HH:mm:ss'),
      words: meta.words || 0,
      source: meta.source || ''
    });
    setPublished_(published);
    reserveArticleProducts_(meta, reserved);
    existingBloggerPosts.push({ title: meta.title, content: html, status: 'LIVE' });
    if (titleKey) existingTitleKeys[titleKey] = true;
    recordSuccess_();
    done++;
    Logger.log('PUBLISHED: ' + meta.title + ' -> ' + post.url);
    notifyOnSuccess_(meta, post);
    if (done < howMany) Utilities.sleep(20000 + Math.floor(Math.random() * 40000));
  }

  if (done === 0) {
    Logger.log('Nothing new to publish. Queue may be empty or already fully published.');
    if (posts.length === 0) notify_('queue is empty', 'The GitHub queue has no articles. Check the generate-daily workflow.');
  }
  return done;
}

function notifyOnSuccess_(meta, post) {
  if (String(cfg('NOTIFY_ON_SUCCESS') || 'false') !== 'true') return;
  notify_('published: ' + meta.title, post.url);
}

// ---------------------------------------------------------------- utilities --
/** Installs the publishing trigger and the automatic queue-refill checker. */
function installTrigger() {
  var existing = ScriptApp.getProjectTriggers();
  var managedHandlers = {
    publishScheduled: true,
    refillQueueIfLow: true,
    auditAmazonProducts: true,
    syncPublishedProductLinks: true
  };
  if (String(cfg('AUTOMATION_ENABLED', 'false')).toLowerCase() !== 'true') {
    existing.forEach(function (trigger) {
      if (managedHandlers[trigger.getHandlerFunction()]) ScriptApp.deleteTrigger(trigger);
    });
    Logger.log('Automation remains DISABLED. Set Script Property AUTOMATION_ENABLED=true only after the Blogger draft is approved.');
    return;
  }
  existing.forEach(function (t) {
    if (managedHandlers[t.getHandlerFunction()]) ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('publishScheduled').timeBased().everyHours(1).create();
  ScriptApp.newTrigger('refillQueueIfLow').timeBased().everyHours(4).create();
  ScriptApp.newTrigger('auditAmazonProducts').timeBased().everyHours(12).create();
  ScriptApp.newTrigger('syncPublishedProductLinks').timeBased().everyHours(4).create();
  Logger.log('Hourly publishing trigger installed. Posting hours: ' + cfg('POST_HOURS') + ' (' + cfg('TZ') + ')');
  Logger.log('Automatic queue-refill checker installed. It checks every 4 hours.');
  Logger.log('Amazon stock/duplicate audit and live/scheduled link sync installed every 12 hours.');
}

/**
 * Automatically dispatches the GitHub refill workflow when fewer than
 * REFILL_MIN_PENDING unpublished, approved v4 articles remain.
 *
 * Requires Script Property GITHUB_ACTIONS_TOKEN (legacy GITHUB_TOKEN is also accepted) with a fine-grained GitHub token
 * that has Actions: Read and write on the Honest Trove automation repository.
 */
function refillQueueIfLow() {
  if (String(cfg('AUTOMATION_ENABLED', 'false')).toLowerCase() !== 'true') {
    Logger.log('Automatic queue refill is paused: AUTOMATION_ENABLED is not true.');
    return;
  }
  removeLegacyPaidLookupToggle_();
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    Logger.log('Refill check skipped: another script execution holds the lock.');
    return;
  }

  try {
    var index = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/index.json');
    var posts = index.posts || [];
    var pending = posts.filter(function (p) {
      return p && p.slug &&
        Number(p.layout_version || 0) >= 4 &&
        p.review_required !== true &&
        productLinksReady_(p) &&
        !isPublished_(p.slug);
    }).length;

    var threshold = Math.max(1, Number(cfg('REFILL_MIN_PENDING', '6')));
    var refillCount = Math.max(1, Math.min(6, Number(cfg('REFILL_COUNT', '6'))));
    Logger.log('Automatic refill check: pending=' + pending + ', threshold=' + threshold);

    if (pending >= threshold) {
      Logger.log('Queue is healthy. No refill needed.');
      return;
    }

    var cooldownMs = Math.max(1, Number(cfg('REFILL_COOLDOWN_HOURS', '12'))) * 3600 * 1000;
    var lastDispatch = Number(P.getProperty('REFILL_LAST_DISPATCH_MS') || 0);
    var remainingMs = cooldownMs - (Date.now() - lastDispatch);
    if (lastDispatch && remainingMs > 0) {
      Logger.log('Refill cooldown active for another ' + Math.ceil(remainingMs / 60000) + ' minutes.');
      return;
    }

    var token = String(P.getProperty('GITHUB_ACTIONS_TOKEN') || P.getProperty('GITHUB_TOKEN') || '').trim();
    if (!token) {
      Logger.log('GITHUB_ACTIONS_TOKEN is missing. Add a GitHub fine-grained token under Project Settings > Script Properties.');
      return;
    }

    var repo = String(cfg('GITHUB_REPO', 'thrustcart-web/easylifehub')).trim();
    var url = 'https://api.github.com/repos/' + repo +
      '/actions/workflows/refill-queue.yml/dispatches';
    var response = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      headers: {
        Authorization: 'Bearer ' + token,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
      },
      payload: JSON.stringify({
        ref: 'main',
        inputs: {
          count: String(refillCount),
          mode: 'auto',
          // Refill uses SerpApi first. Apify is a separately guarded fallback;
          // the GitHub spend variables must also allow each paid provider.
          allow_paid_lookup: true
        }
      }),
      muteHttpExceptions: true
    });

    var status = response.getResponseCode();
    if (status !== 204) {
      throw new Error('GitHub workflow dispatch failed (' + status + '): ' +
        response.getContentText().slice(0, 500));
    }

    P.setProperty('REFILL_LAST_DISPATCH_MS', String(Date.now()));
    Logger.log('REFILL DISPATCHED: GitHub will generate up to ' + refillCount + ' new articles.');
  } catch (err) {
    Logger.log('AUTOMATIC REFILL ERROR: ' + err);
    notify_('Honest Trove automatic refill failed', String(err).slice(0, 800));
  } finally {
    lock.releaseLock();
  }
}

/** Connectivity + permission check (run once after setup). */
function testConnection() {
  try {
    var blog = bloggerCall_('GET', '/blogs/' + cfg('BLOG_ID'));
    Logger.log('OK  blog name : ' + blog.name);
    Logger.log('OK  blog url  : ' + blog.url);
    Logger.log('OK  account   : using the Google account currently running this Apps Script');
    var index = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/index.json');
    Logger.log('OK  queue     : ' + (index.count || 0) + ' articles waiting');
    Logger.log('OK  published : ' + getPublished_().length + ' so far');
    Logger.log('OK  blog url  : ' + (blogUrl_() || '(unknown)'));
    Logger.log('');
    Logger.log('Blogger connection and queue access are working.');
    Logger.log('Next safe step: run createQueuePreviewDraft() and inspect the draft in Blogger.');
    Logger.log('Do not run installTrigger() until the draft is approved and AUTOMATION_ENABLED is explicitly true.');
  } catch (err) {
    Logger.log('FAILED: ' + err);
    Logger.log('');
    Logger.log('Checklist:');
    Logger.log(' 1. The Google account currently running Apps Script must own the blog or be invited as Author/Admin');
    Logger.log(' 2. Blogger API v3 is enabled in that Google Cloud project');
    Logger.log(' 3. Accept any Blogger invitation sent to that Google account');
    Logger.log(' 4. BLOG_ID is correct (from the blog URL: blogger.com/blog/posts/1234567890)');
    Logger.log(' 5. RAW_BASE points at a PUBLIC repo raw URL, branch included');
  }
}



/**
 * Create a Blogger draft from the next pending article with the approved V4 layout.
 * Does not mark the item published, modify the queue or publish live.
 */
function createQueuePreviewDraft() {
  var index = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/index.json');
  var state = fetchJson_(cfg('RAW_BASE') + '/state.json');
  var posts = index.posts || [];
  var generated = (state.generated || []).slice().sort(function (a, b) {
    return String(b.ts || '').localeCompare(String(a.ts || ''));
  });
  var blogPosts = listBlogPostsByStatus_('LIVE')
    .concat(listBlogPostsByStatus_('SCHEDULED'))
    .concat(listBlogPostsByStatus_('DRAFT'));
  var titles = {};
  blogPosts.forEach(function (post) {
    var key = normalizedArticleTitleKey_(post.title || '');
    if (key) titles[key] = true;
  });
  var reserved = {};
  parseJsonArrayProperty_('BLOG_DRAFT_SLUGS').forEach(function (slug) { reserved[String(slug)] = true; });

  var candidate = null;
  var candidateHtml = '';
  for (var i = 0; i < generated.length; i++) {
    var slug = generated[i] && generated[i].slug;
    if (!slug || isPublished_(slug) || reserved[String(slug)] || isDraftedSlug_(slug)) continue;
    var item = posts.filter(function (p) { return p && p.slug === slug; })[0];
    if (!item || item.review_required === true ||
        !productLinksReady_(item) || Number(item.layout_version || 0) < 4) continue;
    var titleKey = normalizedArticleTitleKey_(item.title || '');
    if (titleKey && titles[titleKey]) {
      Logger.log('Skipping duplicate Blogger title: ' + item.title);
      continue;
    }
    var html = fetchText_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/' + item.html_file);
    if (!amazonButtonLayoutReady_(html, item)) continue;
    if (isDuplicateArticleContent_(html, item.title, blogPosts)) {
      Logger.log('Skipping duplicate Blogger article body: ' + item.title);
      continue;
    }
    candidate = item;
    candidateHtml = html;
    break;
  }

  if (!candidate) {
    throw new Error('No new, unique, approved article with fresh verified Amazon links is available for preview.');
  }
  var html = sanitizePostHtml_(ensureAffiliateDisclosures_(candidateHtml));
  var draft = bloggerCall_('POST', '/blogs/' + cfg('BLOG_ID') + '/posts',
    { title: candidate.title, content: html, labels: (candidate.labels || []).filter(Boolean) },
    { isDraft: true });
  if (!draft || !draft.id) throw new Error('Blogger did not return a draft ID.');

  reserveDraftSlug_(candidate.slug);
  Logger.log('DRAFT CREATED: ' + candidate.title);
  Logger.log('DRAFT SLUG: ' + candidate.slug);
  Logger.log('DRAFT ID: ' + draft.id);
  Logger.log('DRAFT URL: ' + (draft.url || 'Open Blogger > Posts > Drafts'));
  Logger.log('Queue slug reserved to prevent second publication.');
  return { title: candidate.title, slug: candidate.slug, draftId: String(draft.id), url: draft.url || '' };
}
/** Publish immediately regardless of the hour (for testing). */
function publishNow() {
  if (breakerOpen_()) return;
  try { publishNext_(1); }
  catch (err) { recordFailure_(err); Logger.log('FAILED: ' + err); }
}

/**
 * After the GitHub product-link backfill succeeds, patch already-published
 * Blogger posts so their old Amazon search URLs become direct /dp/ASIN pages.
 * This only touches posts whose every product has a verified ASIN.
 */
function listBlogPostsByStatus_(status) {
  var posts = [];
  var pageToken = '';
  var guard = 0;
  do {
    var params = { status: String(status || '').toLowerCase(), fetchBodies: true, maxResults: 500, view: 'ADMIN' };
    if (pageToken) params.pageToken = pageToken;
    var page = bloggerCall_('GET', '/blogs/' + cfg('BLOG_ID') + '/posts', null, params);
    (page.items || []).forEach(function (post) {
      post._easyLifeStatus = String(status || '').toUpperCase();
      posts.push(post);
    });
    pageToken = page.nextPageToken || '';
    guard++;
  } while (pageToken && guard < 20);
  return posts;
}

/**
 * Refresh the Blogger title cache and the current custom-domain URL from
 * Blogger itself. Does not create, edit, delete, or publish any blog post.
 */
/**
 * Retag every Amazon US product link in existing LIVE, SCHEDULED and DRAFT posts.
 * Safe to repeat. Only amazon.com product detail links are changed.
 */
function retagBloggerAmazonLinks() {
  var oldTag = 'ezylifehub-20';
  var newTag = 'hotr0e-20';
  var posts = listBlogPostsByStatus_('LIVE')
    .concat(listBlogPostsByStatus_('SCHEDULED'))
    .concat(listBlogPostsByStatus_('DRAFT'));
  var updatedPosts = 0;
  var updatedLinks = 0;
  var errors = 0;
  var titles = {};

  posts.forEach(function (post) {
    var title = String(post.title || '').trim();
    if (title) titles[title] = true;
    if (!post || !post.id) return;

    var before = String(post.content || '');
    var changedLinks = 0;
    var after = before.replace(
      /\bhref\s*=\s*(["'])(https?:\/\/(?:www\.)?amazon\.com\/(?:dp|gp\/product)\/[A-Z0-9]{10}[^"']*)\1/gi,
      function (full, quote, rawUrl) {
        var url = String(rawUrl || '').replace(/&amp;/gi, '&');
        var queryTag = /([?&]tag=)[^&#]*/i;
        var nextUrl;
        if (queryTag.test(url)) {
          nextUrl = url.replace(queryTag, '$1' + newTag);
        } else {
          nextUrl = url + (url.indexOf('?') >= 0 ? '&' : '?') + 'tag=' + newTag;
        }
        if (nextUrl === url) return full;
        changedLinks++;
        return 'href=' + quote + nextUrl.replace(/&/g, '&amp;') + quote;
      }
    );

    if (changedLinks && after !== before) {
      try {
        bloggerCall_('PATCH', '/blogs/' + cfg('BLOG_ID') + '/posts/' + post.id, {
          content: after
        });
        updatedPosts++;
        updatedLinks += changedLinks;
        Logger.log('RETAGGED ' + changedLinks + ' Amazon link(s): ' + title);
        Utilities.sleep(350);
      } catch (err) {
        errors++;
        Logger.log('RETAG FAILED: ' + title + ' | ' + String(err).slice(0, 300));
      }
    }
  });

  P.setProperty('BLOGGER_TITLES', JSON.stringify(Object.keys(titles)));
  var blog = bloggerCall_('GET', '/blogs/' + cfg('BLOG_ID'));
  var currentUrl = String(blog.url || '').replace(/\/+$/, '');
  if (currentUrl) P.setProperty('BLOG_URL', currentUrl);

  Logger.log(
    'Honest Trove Amazon tag migration: old=' + oldTag + ', new=' + newTag +
    ', posts updated=' + updatedPosts + ', links updated=' + updatedLinks +
    ', errors=' + errors + ', titles refreshed=' + Object.keys(titles).length
  );
  Logger.log('Current Blogger URL: ' + currentUrl);
  return {
    postsUpdated: updatedPosts,
    linksUpdated: updatedLinks,
    errors: errors,
    titleCount: Object.keys(titles).length,
    blogUrl: currentUrl
  };
}

function refreshBloggerTitlesOnly() {
  var posts = listBlogPostsByStatus_('LIVE')
    .concat(listBlogPostsByStatus_('SCHEDULED'))
    .concat(listBlogPostsByStatus_('DRAFT'));
  var titleSet = {};
  posts.forEach(function (post) {
    var title = String(post.title || '').trim();
    if (title) titleSet[title] = true;
  });

  var titles = Object.keys(titleSet);
  P.setProperty('BLOGGER_TITLES', JSON.stringify(titles));

  var blog = bloggerCall_('GET', '/blogs/' + cfg('BLOG_ID'));
  var currentUrl = String(blog.url || '').replace(/\/+$/, '');
  if (currentUrl) P.setProperty('BLOG_URL', currentUrl);

  Logger.log('Blogger title cache refreshed: ' + titles.length + ' unique titles.');
  Logger.log('Current Blogger name: ' + String(blog.name || '(not returned)'));
  Logger.log('Current Blogger URL: ' + (currentUrl || '(not returned)'));
  Logger.log('No Blogger posts were modified.');
  return { titleCount: titles.length, blogName: blog.name || '', blogUrl: currentUrl };
}

function normalizedArticleTitleKey_(title) {
  return String(title || '').replace(/<[^>]*>/g, ' ')
    .replace(/&nbsp;|&#160;/gi, ' ').replace(/&amp;/gi, ' and ')
    .toLowerCase().replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
}

function articleShingles_(html) {
  var value = String(html || '')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script\s*>/gi, ' ')
    .replace(/<style\b[^>]*>[\s\S]*?<\/style\s*>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;|&#160;/gi, ' ').replace(/&amp;/gi, ' and ')
    .replace(/&quot;|&#34;/gi, ' ').replace(/&#39;|&apos;/gi, ' ')
    .toLowerCase().replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
  var words = value ? value.split(' ') : [];
  if (words.length < 180) return null;
  var set = {};
  for (var i = 0; i <= words.length - 5; i++) set[words.slice(i, i + 5).join(' ')] = true;
  return { set: set, size: Object.keys(set).length, words: words.length };
}

function duplicateArticleSimilarity_(contentA, contentB) {
  var a = articleShingles_(contentA);
  var b = articleShingles_(contentB);
  if (!a || !b || Math.min(a.size, b.size) < 100) return null;
  var intersection = 0;
  Object.keys(a.set).forEach(function (key) { if (b.set[key]) intersection++; });
  var union = a.size + b.size - intersection;
  return {
    jaccard: union ? intersection / union : 0,
    containment: intersection / Math.min(a.size, b.size),
    wordsA: a.words,
    wordsB: b.words
  };
}

function isDuplicateArticleContent_(content, title, posts) {
  var key = normalizedArticleTitleKey_(title);
  return (posts || []).some(function (post) {
    var score = duplicateArticleSimilarity_(content, post.content || post.summary || '');
    if (!score) return false;
    var sameTitle = key && key === normalizedArticleTitleKey_(post.title || '');
    return (score.jaccard >= 0.95 && score.containment >= 0.97) ||
      (sameTitle && score.jaccard >= 0.82 && score.containment >= 0.92);
  });
}

function duplicateBloggerPosts_() {
  var posts = listBlogPostsByStatus_('LIVE')
    .concat(listBlogPostsByStatus_('SCHEDULED'))
    .concat(listBlogPostsByStatus_('DRAFT'));
  var rank = { LIVE: 0, SCHEDULED: 1, DRAFT: 2 };
  posts.sort(function (a, b) {
    var ar = rank[String(a._easyLifeStatus || '').toUpperCase()];
    var br = rank[String(b._easyLifeStatus || '').toUpperCase()];
    if (ar !== br) return ar - br;
    return String(a.published || a.updated || '').localeCompare(String(b.published || b.updated || ''));
  });

  var keepers = [];
  var duplicates = [];
  posts.forEach(function (post) {
    var key = normalizedArticleTitleKey_(post.title || '');
    var found = null;
    for (var i = 0; i < keepers.length; i++) {
      var keep = keepers[i];
      var score = duplicateArticleSimilarity_(
        post.content || post.summary || '', keep.content || keep.summary || ''
      );
      if (!score) continue;
      var sameTitle = key && key === normalizedArticleTitleKey_(keep.title || '');
      if ((score.jaccard >= 0.95 && score.containment >= 0.97) ||
          (sameTitle && score.jaccard >= 0.82 && score.containment >= 0.92)) {
        found = { post: keep, score: score };
        break;
      }
    }
    if (found) duplicates.push({ duplicate: post, keep: found.post, score: found.score });
    else keepers.push(post);
  });
  return duplicates;
}

/** Read-only scan; inspect its log before running cleanupDuplicateArticles(). */
function previewDuplicateArticles() {
  var rows = duplicateBloggerPosts_();
  Logger.log('Duplicate scan finished. Candidate duplicate posts=' + rows.length);
  rows.forEach(function (row, i) {
    Logger.log(
      (i + 1) + '. status=' + row.duplicate._easyLifeStatus +
      ' | duplicate=' + row.duplicate.title + ' | postId=' + row.duplicate.id +
      ' | keepPostId=' + row.keep.id +
      ' | similarity=' + row.score.jaccard.toFixed(3) +
      ' | containment=' + row.score.containment.toFixed(3)
    );
  });
  return rows.map(function (row) {
    return {
      duplicatePostId: String(row.duplicate.id),
      duplicateTitle: String(row.duplicate.title || ''),
      duplicateStatus: String(row.duplicate._easyLifeStatus || ''),
      keepPostId: String(row.keep.id),
      keepTitle: String(row.keep.title || ''),
      similarity: row.score.jaccard
    };
  });
}

/** Deletes only highly-confident duplicates identified by the same conservative detector. */
function cleanupDuplicateArticles() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) throw new Error('Duplicate cleanup skipped: another script execution is running.');
  try {
    var rows = duplicateBloggerPosts_();
    var deleted = 0;
    rows.forEach(function (row) {
      bloggerCall_('DELETE', '/blogs/' + cfg('BLOG_ID') + '/posts/' + row.duplicate.id);
      deleted++;
      Logger.log('Deleted duplicate postId=' + row.duplicate.id + ' | kept postId=' + row.keep.id +
        ' | title=' + row.duplicate.title);
      Utilities.sleep(400);
    });
    Logger.log('Duplicate cleanup complete. Deleted=' + deleted);
    return deleted;
  } finally {
    lock.releaseLock();
  }
}

function extractAmazonAsins_(html) {
  var found = {};
  var anchors = String(html || '').match(/<a\b[^>]*>/gi) || [];
  anchors.forEach(function (anchor) {
    var href = anchor.match(/\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')/i);
    if (!href) return;
    var url = String(href[1] || href[2] || '').replace(/&amp;/gi, '&');
    var asin = url.match(/https?:\/\/(?:www\.)?amazon\.com\/dp\/([A-Z0-9]{10})(?:[/?&#]|$)/i);
    if (asin) found[asin[1].toUpperCase()] = true;
  });
  return Object.keys(found);
}

function amazonLinkAsin_(anchorAttrs) {
  var href = String(anchorAttrs || '').match(/\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')/i);
  if (!href) return '';
  var url = String(href[1] || href[2] || '').replace(/&amp;/gi, '&');
  var match = url.match(/https?:\/\/(?:www\.)?amazon\.com\/dp\/([A-Z0-9]{10})(?:[/?&#]|$)/i);
  return match ? match[1].toUpperCase() : '';
}

function disableAmazonLinksForAsins_(html, disallowed) {
  return String(html || '').replace(/<a\b([^>]*)>([\s\S]*?)<\/a\s*>/gi, function (full, attrs, inner) {
    var asin = amazonLinkAsin_(attrs);
    if (!asin || !disallowed[asin]) return full;
    return '<span class="amazon-unavailable">' + inner + '</span>';
  });
}

function postSlugMaps_(indexPosts) {
  var byTitle = {};
  var duplicateTitles = {};
  var byPostId = {};
  (indexPosts || []).forEach(function (meta) {
    var key = String(meta.title || '').trim().toLowerCase();
    if (!key) return;
    if (Object.prototype.hasOwnProperty.call(byTitle, key)) duplicateTitles[key] = true;
    else byTitle[key] = meta.slug;
  });
  Object.keys(duplicateTitles).forEach(function (key) { byTitle[key] = ''; });
  getPublished_().forEach(function (post) {
    if (post && post.post_id && post.slug) byPostId[String(post.post_id)] = post.slug;
  });
  return { byTitle: byTitle, byPostId: byPostId };
}

/**
 * Reads all LIVE and SCHEDULED Blogger posts, reserves their existing products,
 * then starts the GitHub audit. Queue-managed posts are prioritised by their
 * Blogger slug; unmanaged posts become external ASIN reservations.
 */
function auditAmazonProducts() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    Logger.log('Amazon stock audit skipped: another script execution is running.');
    return;
  }
  try {
    var token = String(P.getProperty('GITHUB_ACTIONS_TOKEN') || P.getProperty('GITHUB_TOKEN') || '').trim();
    if (!token) throw new Error('GITHUB_ACTIONS_TOKEN is missing; cannot dispatch the product audit workflow.');

    var index = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/index.json');
    var queuePosts = index.posts || [];
    var maps = postSlugMaps_(queuePosts);
    var blogPosts = listBlogPostsByStatus_('LIVE')
      .concat(listBlogPostsByStatus_('SCHEDULED'))
      .concat(listBlogPostsByStatus_('DRAFT'));
    var statusRank = { LIVE: 0, SCHEDULED: 1, DRAFT: 2 };
    blogPosts.sort(function (a, b) {
      var statusA = statusRank[String(a._easyLifeStatus || '').toUpperCase()];
      var statusB = statusRank[String(b._easyLifeStatus || '').toUpperCase()];
      if (statusA !== statusB) return statusA - statusB;
      return String(a.published || a.updated || '').localeCompare(String(b.published || b.updated || ''));
    });
    var externalAsins = {};
    var managedSlugs = [];
    var managedSeen = {};
    var liveCount = 0;
    var scheduledCount = 0;
    var draftCount = 0;
    var bloggerTitles = {};

    blogPosts.forEach(function (post) {
      var status = String(post._easyLifeStatus || post.status || '').toUpperCase();
      if (status === 'SCHEDULED') scheduledCount++;
      else if (status === 'DRAFT') draftCount++;
      else liveCount++;
      var title = String(post.title || '').trim();
      if (title) bloggerTitles[title] = true;
      var slug = maps.byPostId[String(post.id)] ||
        maps.byTitle[String(post.title || '').trim().toLowerCase()] || '';
      if (slug) {
        if (!managedSeen[slug]) {
          managedSeen[slug] = true;
          managedSlugs.push(slug);
        }
      } else {
        extractAmazonAsins_(post.content || '').forEach(function (asin) {
          externalAsins[asin] = true;
        });
      }
    });

    var reservedExternal = Object.keys(externalAsins).sort();
    var reservedManaged = managedSlugs;
    P.setProperty('BLOG_EXTERNAL_ASINS', JSON.stringify(reservedExternal));
    P.setProperty('BLOG_RESERVED_SLUGS', JSON.stringify(reservedManaged));
    P.setProperty('BLOGGER_TITLES', JSON.stringify(Object.keys(bloggerTitles)));

    var repo = String(cfg('GITHUB_REPO', 'thrustcart-web/easylifehub')).trim();
    var url = 'https://api.github.com/repos/' + repo +
      '/actions/workflows/backfill-product-links.yml/dispatches';
    var response = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      headers: {
        Authorization: 'Bearer ' + token,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
      },
      payload: JSON.stringify({
        ref: 'main',
        inputs: {
          allow_paid_lookup: false,
          external_asins: JSON.stringify(reservedExternal),
          published_slugs: JSON.stringify(reservedManaged),
          blogger_titles: JSON.stringify(Object.keys(bloggerTitles))
        }
      }),
      muteHttpExceptions: true
    });
    var httpStatus = response.getResponseCode();
    if (httpStatus !== 204) {
      throw new Error('Amazon product audit dispatch failed (' + httpStatus + '): ' +
        response.getContentText().slice(0, 500));
    }
    P.setProperty('AMAZON_AUDIT_LAST_DISPATCH_MS', String(Date.now()));
    Logger.log(
      'Amazon audit dispatched. Blogger posts: live=' + liveCount +
      ', scheduled=' + scheduledCount +
      ', drafts=' + draftCount +
      ', queue-managed=' + reservedManaged.length +
      ', external ASIN reservations=' + reservedExternal.length +
      ', Blogger titles=' + Object.keys(bloggerTitles).length
    );
  } finally {
    lock.releaseLock();
  }
}

/**
 * One-time (and safe to repeat) migration for existing affiliate posts.
 * Covers LIVE, SCHEDULED, and DRAFT Blogger posts without changing article copy,
 * product destinations, labels, or publication dates.
 */
function syncAffiliateDisclosures() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    Logger.log('Affiliate disclosure sync skipped: another execution is running.');
    return;
  }
  try {
    var posts = listBlogPostsByStatus_('LIVE')
      .concat(listBlogPostsByStatus_('SCHEDULED'))
      .concat(listBlogPostsByStatus_('DRAFT'));
    var updated = 0;
    var checked = 0;
    var noAmazonLinks = 0;
    var errors = 0;

    posts.forEach(function(post) {
      if (!post || !post.id) return;
      var current = String(post.content || '');
      if (!/amazon\.com/i.test(current)) {
        noAmazonLinks++;
        return;
      }
      checked++;
      var revised = ensureAffiliateDisclosures_(current);
      if (revised === current) return;
      try {
        bloggerCall_('PATCH', '/blogs/' + cfg('BLOG_ID') + '/posts/' + post.id, {
          content: revised
        });
        updated++;
        Logger.log('DISCLOSURE UPDATED: ' + post.title + ' | ' + post._easyLifeStatus);
        Utilities.sleep(350);
      } catch (err) {
        errors++;
        Logger.log('DISCLOSURE UPDATE FAILED: ' + post.title + ' | ' + err);
      }
    });

    Logger.log(
      'Affiliate disclosure migration complete. Checked=' + checked +
      ', updated=' + updated + ', already current=' + (checked - updated - errors) +
      ', no Amazon links=' + noAmazonLinks + ', errors=' + errors
    );
    return { checked: checked, updated: updated, noAmazonLinks: noAmazonLinks, errors: errors };
  } finally {
    lock.releaseLock();
  }
}

/**
 * Sync audited queue HTML to existing live/scheduled Blogger posts, and disable
 * any unmanaged Amazon link that is out of stock, stale, or duplicated.
 */
function syncPublishedProductLinks() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    Logger.log('Product-link sync skipped: another execution is running.');
    return;
  }
  try {
    var index = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/index.json');
    var audit;
    try {
      audit = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/product-audit.json');
    } catch (auditErr) {
      Logger.log('Product-link sync skipped: stock audit has not completed yet.');
      return;
    }
    var stockByAsin = audit.stock_by_asin || {};
    var bySlug = {};
    (index.posts || []).forEach(function (meta) {
      if (!meta || !meta.slug) return;
      bySlug[meta.slug] = meta;
      // A newly generated item may be newer than the last site-wide audit.
      // Only a fresh, explicitly sourced SerpApi or Apify stock check is valid until it expires.
      (meta.items || []).forEach(function (item) {
        var asin = String(item && item.asin || '').toUpperCase();
        var checkedAt = Number(item && item.stock_checked_at || 0);
        var stockSource = String(item && item.stock_source || '').toLowerCase();
        var existing = stockByAsin[asin];
        if (/^[A-Z0-9]{10}$/.test(asin) && (stockSource === 'apify' || stockSource === 'serpapi') &&
            checkedAt > Number(existing && existing.stock_checked_at || 0)) {
          stockByAsin[asin] = {
            parent_asin: String(item.parent_asin || asin).toUpperCase(),
            availability_type: String(item.availability_type || 'UNKNOWN').toUpperCase(),
            stock_checked_at: checkedAt,
            stock_source: stockSource
          };
        }
      });
    });
    var maps = postSlugMaps_(index.posts || []);
    var blogPosts = listBlogPostsByStatus_('LIVE')
      .concat(listBlogPostsByStatus_('SCHEDULED'))
      .concat(listBlogPostsByStatus_('DRAFT'));
    var statusRank = { LIVE: 0, SCHEDULED: 1, DRAFT: 2 };
    blogPosts.sort(function (a, b) {
      var statusA = statusRank[String(a._easyLifeStatus || a.status || '').toUpperCase()];
      var statusB = statusRank[String(b._easyLifeStatus || b.status || '').toUpperCase()];
      if (statusA !== statusB) return statusA - statusB;
      return String(a.published || a.updated || '').localeCompare(String(b.published || b.updated || ''));
    });

    var usedAsins = {};
    var usedParents = {};
    var updated = 0;
    var blockedLinks = 0;
    var skipped = 0;
    var now = Math.floor(Date.now() / 1000);

    blogPosts.forEach(function (post) {
      if (!post || !post.id) return;
      var slug = maps.byPostId[String(post.id)] ||
        maps.byTitle[String(post.title || '').trim().toLowerCase()] || '';
      var meta = slug ? bySlug[slug] : null;
      var rawPostContent = String(post.content || '');
      var originalContent = sanitizePostHtml_(rawPostContent);
      var hasEditorialEnhancement = rawPostContent.indexOf('<!-- honest-trove-enhancement:v1 -->') !== -1;
      // Once an article has been editorially enhanced, Blogger becomes the
      // source of truth for its body. Queue HTML is older and must never
      // overwrite the added editorial sections during a later stock sync.
      var html = hasEditorialEnhancement
        ? rawPostContent
        : (meta && meta.html_file
          ? fetchText_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/' + meta.html_file)
          : rawPostContent);
      // Preserve every direct Amazon product anchor in existing articles.
      // A stale/missing stock audit must never turn a useful product link into
      // dead text. Amazon itself can show availability on the destination page.
      var preservedLinks = 0;
      html = html.replace(/<a\b([^>]*)>([\s\S]*?)<\/a\s*>/gi, function (full, attrs, inner) {
        var asin = amazonLinkAsin_(attrs);
        if (!asin) return full;
        preservedLinks++;
        var stock = stockByAsin[asin];
        var parent = String(stock && stock.parent_asin || asin).toUpperCase();
        // Reserve product families only when the audit is fresh and trusted.
        // This affects future product selection, never the clickability here.
        var status = String(stock && stock.availability_type || '').toUpperCase();
        var checkedAt = Number(stock && stock.stock_checked_at || 0);
        var stockSource = String(stock && stock.stock_source || '').toLowerCase();
        var fresh = (stockSource === 'apify' || stockSource === 'serpapi') &&
          checkedAt > 0 && now >= checkedAt &&
          (now - checkedAt) <= AMAZON_STOCK_MAX_AGE_SECONDS;
        if (AMAZON_STOCK_ALLOWED[status] && fresh) {
          usedAsins[asin] = true;
          usedParents[parent] = true;
        }
        return full;
      });
      if (preservedLinks) {
        Logger.log('PRESERVED AMAZON LINKS: ' + post.title + ' | links=' + preservedLinks);
      }

      html = ensureAffiliateDisclosures_(html);
      html = sanitizePostHtml_(html);
      if (html !== originalContent) {
        bloggerCall_('PATCH', '/blogs/' + cfg('BLOG_ID') + '/posts/' + post.id, {
          content: html
        });
        updated++;
        Logger.log('SYNCED STOCK-SAFE LINKS: ' + post.title + ' (' + String(post.status || '') + ')');
        Utilities.sleep(500);
      } else {
        // Even if content did not change, this post reserves its products for later articles.
      }
    });

    var externalProducts = audit.external_products || {};
    var externalAsins = [];
    var externalParents = [];
    Object.keys(externalProducts).forEach(function (asin) {
      externalAsins.push(asin);
      var parent = String(externalProducts[asin].parent_asin || asin).toUpperCase();
      externalParents.push(parent);
    });
    P.setProperty('BLOG_EXTERNAL_ASINS', JSON.stringify(externalAsins));
    P.setProperty('BLOG_EXTERNAL_PARENT_ASINS', JSON.stringify(externalParents));
    P.setProperty('AMAZON_AUDIT_LAST_SYNC_MS', String(Date.now()));
    Logger.log(
      'Stock-safe product sync complete. Updated posts=' + updated +
      ', disabled links=' + blockedLinks + ', skipped=' + skipped
    );
  } finally {
    lock.releaseLock();
  }
}


/**
 * Published-content enhancement bridge.
 * Exports every LIVE Blogger post to a backed-up GitHub input file, dispatches
 * the AI enhancement workflow, then safely applies the completed HTML in
 * small batches. Only post.content is patched; title, labels, status and dates
 * are never sent in the update request.
 */
function enhancementGitHubToken_() {
  var token = String(P.getProperty('GITHUB_ACTIONS_TOKEN') || P.getProperty('GITHUB_TOKEN') || '').trim();
  if (!token) {
    throw new Error('GITHUB_ACTIONS_TOKEN is missing from Apps Script Project Settings > Script Properties.');
  }
  return token;
}

function enhancementGitHubRequest_(method, path, payload, allowNotFound) {
  var repo = String(cfg('GITHUB_REPO', 'thrustcart-web/easylifehub')).trim();
  var url = 'https://api.github.com/repos/' + repo + '/' + String(path || '').replace(/^\/+/, '');
  var options = {
    method: method,
    headers: {
      Authorization: 'Bearer ' + enhancementGitHubToken_(),
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    muteHttpExceptions: true
  };
  if (payload) {
    options.contentType = 'application/json';
    options.payload = JSON.stringify(payload);
  }
  var response = UrlFetchApp.fetch(url, options);
  var code = response.getResponseCode();
  var body = response.getContentText();
  if (allowNotFound && code === 404) return null;
  if (code === 403) {
    throw new Error('GitHub API 403 for ' + path +
      ': GITHUB_ACTIONS_TOKEN in Apps Script needs Contents: Read and write and Actions: Read and write permissions on ' +
      repo + '. ' + body.slice(0, 300));
  }
  if (code < 200 || code >= 300) {
    throw new Error('GitHub API ' + code + ' for ' + path + ': ' + body.slice(0, 500));
  }
  return body ? JSON.parse(body) : {};
}

function enhancementGitHubPutJson_(path, value, message) {
  var encodedPath = String(path).split('/').map(encodeURIComponent).join('/');
  var existing = enhancementGitHubRequest_('get', 'contents/' + encodedPath + '?ref=main', null, true);
  var textValue = JSON.stringify(value);
  var content = Utilities.base64Encode(Utilities.newBlob(textValue, 'application/json').getBytes());
  var payload = {
    message: message,
    content: content,
    branch: 'main'
  };
  if (existing && existing.sha) payload.sha = existing.sha;
  return enhancementGitHubRequest_('put', 'contents/' + encodedPath, payload, false);
}

function enhancementGitHubGetJson_(path) {
  // Read the public raw file directly so large multi-article results are not
  // subject to the GitHub Contents API's base64 response-size limit.
  var repo = String(cfg('GITHUB_REPO', 'thrustcart-web/easylifehub')).trim();
  var rawPath = String(path).split('/').map(encodeURIComponent).join('/');
  var url = 'https://raw.githubusercontent.com/' + repo + '/main/' + rawPath;
  var response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  var code = response.getResponseCode();
  if (code < 200 || code >= 300) {
    throw new Error('GitHub enhancement output is not ready (' + code + '): ' + path);
  }
  return JSON.parse(response.getContentText());
}

function enhancementSha256_(value) {
  var bytes = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    String(value || ''),
    Utilities.Charset.UTF_8
  );
  return bytes.map(function (byte) {
    var unsigned = byte < 0 ? byte + 256 : byte;
    return ('0' + unsigned.toString(16)).slice(-2);
  }).join('');
}

function enhancementAnchorHrefs_(html) {
  var hrefs = [];
  String(html || '').replace(/<a\b([^>]*)>/gi, function (tag, attrs) {
    var match = /\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i.exec(attrs || '');
    if (match) {
      for (var i = 1; i <= 3; i++) {
        if (match[i] !== undefined) {
          hrefs.push(String(match[i]));
          break;
        }
      }
    }
    return tag;
  });
  return hrefs;
}

function enhancementIsAmazonHref_(href) {
  var value = String(href || '').replace(/&amp;/gi, '&').trim();
  var match = /^https?:\/\/([^\/?#]+)/i.exec(value);
  if (!match) return false;
  var host = match[1].toLowerCase().replace(/:\d+$/, '');
  return host === 'amazon.com' || host.endsWith('.amazon.com') ||
    host === 'amzn.to' || host === 'amzn.com';
}

function enhancementSameAmazonLinks_(before, after) {
  var first = enhancementAnchorHrefs_(before).filter(enhancementIsAmazonHref_);
  var secondAll = enhancementAnchorHrefs_(after);
  var second = secondAll.filter(enhancementIsAmazonHref_);
  if (first.length !== second.length) return false;
  for (var i = 0; i < first.length; i++) {
    if (first[i] !== second[i]) return false;
  }
  return secondAll.every(enhancementIsAmazonHref_);
}

/**
 * START: capture all currently published Blogger posts and queue an AI job.
 * Run this once. The full original HTML is backed up in the GitHub job input.
 */
function startPublishedArticleEnhancement() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    Logger.log('Enhancement start skipped: another Apps Script execution is running.');
    return;
  }
  try {
    var activeBatch = String(P.getProperty('PUBLISHED_ENHANCEMENT_BATCH_ID') || '').trim();
    if (activeBatch) {
      throw new Error('An enhancement batch is already active: ' + activeBatch +
        '. Apply its output or finish it before starting another batch.');
    }

    var posts = listBlogPostsByStatus_('LIVE');
    if (!posts.length) {
      Logger.log('No LIVE Blogger posts found. Nothing to enhance.');
      return;
    }

    var batchId = 'ht-enhance-' + Utilities.formatDate(new Date(), 'UTC', 'yyyyMMdd-HHmmss') +
      '-' + Utilities.getUuid().replace(/-/g, '').slice(0, 8);
    var exportPosts = posts.map(function (post) {
      return {
        id: String(post.id || ''),
        title: String(post.title || ''),
        status: 'LIVE',
        published: String(post.published || ''),
        updated: String(post.updated || ''),
        content: String(post.content || '')
      };
    }).filter(function (post) { return post.id && post.content; });

    if (!exportPosts.length) throw new Error('LIVE Blogger posts were found, but none had readable HTML content.');

    var input = {
      schema_version: 1,
      batch_id: batchId,
      blog_id: String(cfg('BLOG_ID')),
      blog_url: blogUrl_(),
      created_at: new Date().toISOString(),
      scope: 'LIVE_ONLY',
      rules: {
        preserve_existing_html_structure: true,
        preserve_amazon_hrefs_exactly: true,
        remove_non_amazon_anchor_links: true,
        append_only_editorial_enhancement: true
      },
      posts: exportPosts
    };
    var inputPath = 'content/enhancement/jobs/' + batchId + '.input.json';
    enhancementGitHubPutJson_(inputPath, input,
      'backup: export LIVE Blogger posts for enhancement ' + batchId);

    var repo = String(cfg('GITHUB_REPO', 'thrustcart-web/easylifehub')).trim();
    var dispatchUrl = 'https://api.github.com/repos/' + repo +
      '/actions/workflows/enhance-published-articles.yml/dispatches';
    var dispatch = UrlFetchApp.fetch(dispatchUrl, {
      method: 'post',
      contentType: 'application/json',
      headers: {
        Authorization: 'Bearer ' + enhancementGitHubToken_(),
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
      },
      payload: JSON.stringify({ ref: 'main', inputs: { batch_id: batchId } }),
      muteHttpExceptions: true
    });
    var status = dispatch.getResponseCode();
    if (status !== 204) {
      throw new Error('Enhancement workflow dispatch failed (' + status + '): ' +
        dispatch.getContentText().slice(0, 500) +
        '. The original backup remains at ' + inputPath);
    }

    P.setProperty('PUBLISHED_ENHANCEMENT_BATCH_ID', batchId);
    P.setProperty('PUBLISHED_ENHANCEMENT_APPLY_INDEX', '0');
    P.setProperty('PUBLISHED_ENHANCEMENT_EXPECTED', String(exportPosts.length));
    P.setProperty('PUBLISHED_ENHANCEMENT_APPLIED', '0');
    P.setProperty('PUBLISHED_ENHANCEMENT_SKIPPED', '0');
    P.setProperty('PUBLISHED_ENHANCEMENT_ERRORS', '0');
    Logger.log('PUBLISHED ARTICLE ENHANCEMENT DISPATCHED');
    Logger.log('Batch ID: ' + batchId);
    Logger.log('LIVE posts exported: ' + exportPosts.length);
    Logger.log('Original HTML backup: ' + inputPath);
    Logger.log('Next: wait for the GitHub workflow to finish successfully, then run applyPublishedArticleEnhancement().');
    return { batchId: batchId, posts: exportPosts.length, inputPath: inputPath };
  } finally {
    lock.releaseLock();
  }
}

/**
 * APPLY: update at most five LIVE posts per execution. Additional one-time
 * triggers continue automatically until all results have been checked.
 */
function applyPublishedArticleEnhancement() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    Logger.log('Enhancement apply skipped: another execution holds the script lock.');
    return;
  }
  try {
    var batchId = String(P.getProperty('PUBLISHED_ENHANCEMENT_BATCH_ID') || '').trim();
    if (!batchId) {
      Logger.log('No active published-article enhancement batch.');
      return;
    }

    var outputPath = 'content/enhancement/jobs/' + batchId + '.output.json';
    var output;
    try {
      output = enhancementGitHubGetJson_(outputPath);
    } catch (readError) {
      Logger.log('Enhancement output is not available yet. Confirm the GitHub workflow succeeded, then run this function again. ' +
        String(readError).slice(0, 350));
      return;
    }
    if (String(output.batch_id || '') !== batchId || !Array.isArray(output.results)) {
      throw new Error('Enhancement output batch ID or result structure is invalid.');
    }

    var livePosts = listBlogPostsByStatus_('LIVE');
    var liveById = {};
    livePosts.forEach(function (post) { liveById[String(post.id)] = post; });

    var results = output.results;
    var start = Math.max(0, Number(P.getProperty('PUBLISHED_ENHANCEMENT_APPLY_INDEX') || 0));
    var end = Math.min(results.length, start + 5);
    var applied = Number(P.getProperty('PUBLISHED_ENHANCEMENT_APPLIED') || 0);
    var skipped = Number(P.getProperty('PUBLISHED_ENHANCEMENT_SKIPPED') || 0);
    var errors = Number(P.getProperty('PUBLISHED_ENHANCEMENT_ERRORS') || 0);

    for (var i = start; i < end; i++) {
      var item = results[i] || {};
      if (item.status !== 'enhanced') {
        skipped++;
        Logger.log('ENHANCEMENT SKIPPED: ' + (item.title || item.post_id || 'unknown') +
          ' | status=' + String(item.status || 'missing') +
          (item.error ? ' | ' + String(item.error).slice(0, 180) : ''));
        continue;
      }

      var postId = String(item.post_id || '');
      var currentPost = liveById[postId];
      if (!currentPost) {
        skipped++;
        Logger.log('ENHANCEMENT SKIPPED: post is no longer LIVE or cannot be found | ' + postId);
        continue;
      }

      var currentHtml = String(currentPost.content || '');
      var revisedHtml = String(item.content || '');
      if (!item.original_sha256 || enhancementSha256_(currentHtml) !== String(item.original_sha256)) {
        skipped++;
        Logger.log('ENHANCEMENT CONFLICT: article changed after export; left untouched | ' + currentPost.title);
        continue;
      }
      if (revisedHtml.indexOf('<!-- honest-trove-enhancement:v1 -->') === -1 ||
          !enhancementSameAmazonLinks_(currentHtml, revisedHtml)) {
        skipped++;
        Logger.log('ENHANCEMENT VALIDATION FAILED: structure/link safety check did not pass | ' + currentPost.title);
        continue;
      }

      try {
        // Content-only PATCH: keep Blogger's original title, labels and dates.
        bloggerCall_('PATCH', '/blogs/' + cfg('BLOG_ID') + '/posts/' + postId, {
          content: revisedHtml
        });
        applied++;
        Logger.log('ENHANCED LIVE ARTICLE: ' + currentPost.title +
          ' | added words=' + Number(item.added_words || 0) +
          ' | provider=' + String(item.provider || 'unknown'));
        Utilities.sleep(350);
      } catch (patchError) {
        errors++;
        Logger.log('ENHANCEMENT UPDATE FAILED: ' + currentPost.title + ' | ' +
          String(patchError).slice(0, 250));
      }
    }

    P.setProperty('PUBLISHED_ENHANCEMENT_APPLY_INDEX', String(end));
    P.setProperty('PUBLISHED_ENHANCEMENT_APPLIED', String(applied));
    P.setProperty('PUBLISHED_ENHANCEMENT_SKIPPED', String(skipped));
    P.setProperty('PUBLISHED_ENHANCEMENT_ERRORS', String(errors));

    if (end < results.length) {
      ScriptApp.getProjectTriggers().forEach(function (trigger) {
        if (trigger.getHandlerFunction() === 'applyPublishedArticleEnhancement') {
          ScriptApp.deleteTrigger(trigger);
        }
      });
      ScriptApp.newTrigger('applyPublishedArticleEnhancement').timeBased().after(60 * 1000).create();
      Logger.log('Enhancement progress: ' + end + '/' + results.length +
        ' checked. The next batch of up to five posts is scheduled automatically.');
    } else {
      Logger.log('PUBLISHED ARTICLE ENHANCEMENT FINISHED');
      Logger.log('Results: total=' + results.length + ', applied=' + applied +
        ', skipped=' + skipped + ', errors=' + errors);
      Logger.log('Backup retained in GitHub: content/enhancement/jobs/' + batchId + '.input.json');
      P.setProperty('PUBLISHED_ENHANCEMENT_LAST_DONE_BATCH_ID', batchId);
      P.deleteProperty('PUBLISHED_ENHANCEMENT_BATCH_ID');
      P.deleteProperty('PUBLISHED_ENHANCEMENT_APPLY_INDEX');
      ScriptApp.getProjectTriggers().forEach(function (trigger) {
        if (trigger.getHandlerFunction() === 'applyPublishedArticleEnhancement') {
          ScriptApp.deleteTrigger(trigger);
        }
      });
    }
    return { checked: end, total: results.length, applied: applied, skipped: skipped, errors: errors };
  } finally {
    lock.releaseLock();
  }
}

/**
 * Roll back the last enhancement batch using its original HTML backup.
 * It only restores posts that still contain this enhancement marker.
 */
function rollbackLastPublishedArticleEnhancement() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    Logger.log('Enhancement rollback skipped: another execution holds the script lock.');
    return;
  }
  try {
    var batchId = String(
      P.getProperty('PUBLISHED_ENHANCEMENT_BATCH_ID') ||
      P.getProperty('PUBLISHED_ENHANCEMENT_LAST_DONE_BATCH_ID') || ''
    ).trim();
    if (!batchId) throw new Error('No enhancement batch is available for rollback.');

    var inputPath = 'content/enhancement/jobs/' + batchId + '.input.json';
    var outputPath = 'content/enhancement/jobs/' + batchId + '.output.json';
    var input = enhancementGitHubGetJson_(inputPath);
    var output = enhancementGitHubGetJson_(outputPath);
    var originals = {};
    (input.posts || []).forEach(function (post) {
      originals[String(post.id)] = String(post.content || '');
    });
    var resultById = {};
    (output.results || []).forEach(function (item) {
      resultById[String(item.post_id)] = item;
    });
    var live = listBlogPostsByStatus_('LIVE');
    var restored = 0;
    var skipped = 0;
    var errors = 0;
    live.forEach(function (post) {
      var postId = String(post.id);
      var originalHtml = originals[postId];
      var result = resultById[postId] || {};
      var currentHtml = String(post.content || '');
      if (!originalHtml || result.status !== 'enhanced' ||
          currentHtml.indexOf('<!-- honest-trove-enhancement:v1 -->') === -1) return;
      // Do not roll back an article that someone edited after the enhancement.
      if (!result.enhanced_sha256 || enhancementSha256_(currentHtml) !== String(result.enhanced_sha256)) {
        skipped++;
        Logger.log('ROLLBACK SKIPPED: article changed after enhancement | ' + post.title);
        return;
      }
      try {
        bloggerCall_('PATCH', '/blogs/' + cfg('BLOG_ID') + '/posts/' + postId, {
          content: sanitizePostHtml_(originalHtml)
        });
        restored++;
        Logger.log('RESTORED ORIGINAL ARTICLE: ' + post.title);
        Utilities.sleep(350);
      } catch (err) {
        errors++;
        Logger.log('ROLLBACK FAILED: ' + post.title + ' | ' + String(err).slice(0, 250));
      }
    });
    Logger.log('ENHANCEMENT ROLLBACK FINISHED: restored=' + restored +
      ', skipped=' + skipped + ', errors=' + errors + ', batch=' + batchId);
    P.setProperty('PUBLISHED_ENHANCEMENT_LAST_ROLLBACK_BATCH_ID', batchId);
    return { restored: restored, skipped: skipped, errors: errors, batchId: batchId };
  } finally {
    lock.releaseLock();
  }
}

/** Full status report. */
function status() {
  removeLegacyPaidLookupToggle_();
  var pub = getPublished_();
  Logger.log('=== AutoBlog status ===');
  Logger.log('Published total : ' + pub.length);
  Logger.log('Published today : ' + countPublishedToday_() + ' / ' + cfg('MAX_PER_DAY'));
  Logger.log('Failures        : ' + (P.getProperty('FAIL_COUNT') || '0'));
  Logger.log('Breaker         : ' + (breakerOpen_() ? 'OPEN (paused)' : 'closed'));
  Logger.log('Posting hours   : ' + cfg('POST_HOURS') + ' ' + cfg('TZ'));
  Logger.log('Automation      : ' + (String(cfg('AUTOMATION_ENABLED', 'false')).toLowerCase() === 'true' ? 'ENABLED' : 'DISABLED'));
  Logger.log('Product lookup  : SerpApi primary + guarded Apify fallback; Apify bulk stock audit');
  var triggers = ScriptApp.getProjectTriggers();
  Logger.log('Triggers        : ' + triggers.length);
  triggers.forEach(function (trigger) {
    Logger.log('  Trigger       : ' + trigger.getHandlerFunction() + ' | ' + trigger.getEventType());
  });
  try {
    var index = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/index.json');
    var allPosts = index.posts || [];
    var pending = allPosts.filter(function (p) {
      return p && p.slug && Number(p.layout_version || 0) >= 4 && p.review_required !== true && productLinksReady_(p) && !isPublished_(p.slug);
    }).length;
    var needsReview = allPosts.filter(function (p) {
      return p && p.slug && p.review_required === true && !isPublished_(p.slug);
    }).length;
    var legacy = allPosts.filter(function (p) {
      return p && Number(p.layout_version || 0) < 4 && !isPublished_(p.slug);
    }).length;
    Logger.log('Queue total     : ' + (index.count || 0));
    Logger.log('Queue pending v4: ' + pending);
    Logger.log('Legacy skipped  : ' + legacy);
    var days = Math.floor(pending / Math.max(1, Number(cfg('MAX_PER_DAY'))));
    Logger.log('Days of content : ~' + days);
  } catch (e) { Logger.log('Queue           : unreachable (' + e + ')'); }
  pub.slice(-5).forEach(function (p) { Logger.log('  last: ' + p.ts + ' | ' + p.title); });
}

/** One-off weekly email so you know it is alive (optional). */
function weeklyDigest() {
  var pub = getPublished_();
  var weekAgo = new Date().getTime() - 7 * 86400 * 1000;
  var recent = pub.filter(function (p) { return new Date(p.ts.replace(' ', 'T')).getTime() >= weekAgo; });
  var body = 'Published in the last 7 days: ' + recent.length + '\nTotal published: ' + pub.length + '\n\n';
  recent.forEach(function (p) { body += '- ' + p.ts + '  ' + p.title + '\n  ' + (p.url || '') + '\n'; });
  notify_('weekly digest', body);
}

/** Install the optional weekly digest trigger (Sunday 20:00). */
function installDigestTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'weeklyDigest') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('weeklyDigest').timeBased().onWeekDay(ScriptApp.WeekDay.SUNDAY).atHour(20).create();
  Logger.log('Weekly digest trigger installed.');
}
