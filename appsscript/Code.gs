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
  POST_HOURS:    '9,13,18',          // local hours, comma separated
  MAX_PER_DAY:   '3',
  JITTER_MAX:    '1200',             // random 0-20 min delay inside the hour
  NOTIFY_EMAIL:  '',                 // blank = your Google account email
  TZ:            'Africa/Cairo',
  MIN_GAP_HOURS: '2',
  GITHUB_REPO: 'thrustcart-web/easylifehub',
  REFILL_MIN_PENDING: '6',
  REFILL_COUNT: '6',
  REFILL_COOLDOWN_HOURS: '12',
  PAID_LOOKUP_ENABLED: 'false',   // explicit opt-in for paid Apify/SerpApi discovery
  // ---- category hub + related posts (topical authority plumbing) ----
  BLOG_URL:        '',                    // blank = auto-detected once from the API
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
  Logger.log('Next: run installTrigger(), then testConnection().');
}

function cfg(key, fallback) {
  var v = P.getProperty(key);
  return (v === null || v === '') ? (fallback || DEFAULTS[key] || '') : v;
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

function insertPost_(title, html, labels) {
  return bloggerCall_('POST', '/blogs/' + cfg('BLOG_ID') + '/posts',
    { title: title, content: html, labels: labels || [] });
}

function updatePost_(postId, title, html, labels) {
  return bloggerCall_('PUT', '/blogs/' + cfg('BLOG_ID') + '/posts/' + postId,
    { title: title, content: html, labels: labels || [] });
}

// ------------------------------------------------- links, related, hub page --
/** The blog's own address. Detected once from the API, then cached. */
function blogUrl_() {
  var url = String(cfg('BLOG_URL') || '').replace(/\/+$/, '');
  if (url) return url;
  try {
    var blog = bloggerCall_('GET', '/blogs/' + cfg('BLOG_ID'));
    url = String(blog.url || '').replace(/\/+$/, '');
    if (url) P.setProperty('BLOG_URL', url);
  } catch (e) {
    Logger.log('blogUrl_: ' + e);
  }
  return url;
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
    if (host === 'amazon.com' || /\.amazon\.com$/.test(host)) return full;
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

/** True only when every item has a verified, tracked Amazon product detail page. */
function productLinksReady_(meta) {
  if (!meta || meta.product_links_verified !== true) return false;
  var items = meta.items || [];
  if (!items.length) return false;
  return items.every(function (item) {
    return item &&
      item.asin_verified === true &&
      /^[A-Z0-9]{10}$/.test(String(item.asin || '')) &&
      String(item.url || '').indexOf('https://www.amazon.com/dp/' + item.asin + '?tag=') === 0;
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
  var tz = cfg('TZ');
  var now = new Date();
  var hour = Number(Utilities.formatDate(now, tz, 'H'));
  var hours = String(cfg('POST_HOURS')).split(',').map(function (h) { return Number(String(h).trim()); });

  Logger.log(Utilities.formatDate(now, tz, 'yyyy-MM-dd HH:mm') + ' ' + tz + ' | posting hours: ' + hours.join(','));

  if (hours.indexOf(hour) === -1) { Logger.log('Not a posting hour - exiting.'); return; }
  if (breakerOpen_()) return;

  // random delay inside the hour -> no machine-perfect timestamps
  var jitterMax = Math.min(Number(cfg('JITTER_MAX') || 1200), 1500);
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

  var done = 0;
  for (var i = 0; i < posts.length && done < howMany; i++) {
    var meta = posts[i];
    if (!meta || !meta.slug || isPublished_(meta.slug)) continue;
    if (meta.review_required === true) {
      Logger.log('Skipping article pending editorial review: ' + meta.slug);
      continue;
    }
    if (!productLinksReady_(meta)) {
      Logger.log('Skipping article: direct Amazon product pages are not verified yet: ' + meta.slug);
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
  existing.forEach(function (t) {
    var handler = t.getHandlerFunction();
    if (handler === 'publishScheduled' || handler === 'refillQueueIfLow') {
      ScriptApp.deleteTrigger(t);
    }
  });
  ScriptApp.newTrigger('publishScheduled').timeBased().everyHours(1).create();
  ScriptApp.newTrigger('refillQueueIfLow').timeBased().everyHours(4).create();
  Logger.log('Hourly publishing trigger installed. Posting hours: ' + cfg('POST_HOURS') + ' (' + cfg('TZ') + ')');
  Logger.log('Automatic queue-refill checker installed. It checks every 4 hours.');
}

/**
 * Automatically dispatches the GitHub refill workflow when fewer than
 * REFILL_MIN_PENDING unpublished, approved v4 articles remain.
 *
 * Requires Script Property GITHUB_ACTIONS_TOKEN (legacy GITHUB_TOKEN is also accepted) with a fine-grained GitHub token
 * that has Actions: Read and write on the EasyLifeHub repository.
 */
function refillQueueIfLow() {
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
          allow_paid_lookup: String(cfg('PAID_LOOKUP_ENABLED', 'false')).toLowerCase() === 'true'
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
    notify_('EasyLifeHub automatic refill failed', String(err).slice(0, 800));
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
    Logger.log('Everything works. Run installTrigger() if you have not, then forget about it.');
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
  var candidate = null;

  // Preview the newest generated article first. Do not accidentally create a
  // Blogger draft from older queue entries that still need editorial review.
  for (var i = 0; i < generated.length; i++) {
    var slug = generated[i] && generated[i].slug;
    if (!slug || isPublished_(slug)) continue;
    var item = posts.filter(function (p) { return p && p.slug === slug; })[0];
    if (
      item &&
      item.review_required !== true &&
      productLinksReady_(item) &&
      Number(item.layout_version || 0) >= 4
    ) {
      candidate = item;
      break;
    }
  }

  if (!candidate) {
    throw new Error('No newly generated, editorially approved article with verified direct Amazon links is available for preview.');
  }

  var html = fetchText_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/' + candidate.html_file);
  if (!amazonButtonLayoutReady_(html, candidate)) {
    throw new Error('The selected article does not use the required Amazon-button-only v4 layout.');
  }
  html = sanitizePostHtml_(html);

  var draft = bloggerCall_('POST', '/blogs/' + cfg('BLOG_ID') + '/posts',
    { title: candidate.title, content: html, labels: (candidate.labels || []).filter(Boolean) },
    { isDraft: true });

  if (!draft || !draft.id) throw new Error('Blogger did not return a draft ID.');
  Logger.log('DRAFT CREATED: ' + candidate.title);
  Logger.log('DRAFT SLUG: ' + candidate.slug);
  Logger.log('DRAFT ID: ' + draft.id);
  Logger.log('DRAFT URL: ' + (draft.url || 'Open Blogger > Posts > Drafts'));
  Logger.log('This preview did not mark the queue item as published.');
  return { title: candidate.title, slug: candidate.slug, draftId: String(draft.id), url: draft.url || '' };
}/** Publish immediately regardless of the hour (for testing). */
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
function syncPublishedProductLinks() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    Logger.log('Product-link sync skipped: another execution is running.');
    return;
  }

  try {
    var index = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/index.json');
    var bySlug = {};
    (index.posts || []).forEach(function (meta) {
      if (meta && meta.slug) bySlug[meta.slug] = meta;
    });

    var published = getPublished_();
    var updated = 0;
    var skipped = 0;

    published.forEach(function (post) {
      var meta = bySlug[post.slug];
      if (!meta || meta.product_links_verified !== true || !meta.html_file) {
        skipped++;
        return;
      }

      var items = meta.items || [];
      var valid = items.length > 0 && items.every(function (item) {
        return item &&
          item.asin_verified === true &&
          /^[A-Z0-9]{10}$/.test(String(item.asin || '')) &&
          /^https:\/\/www\.amazon\.com\/dp\/[A-Z0-9]{10}\?tag=[^&\s]+$/.test(String(item.url || ''));
      });
      if (!valid || !post.post_id) {
        skipped++;
        return;
      }

      var html = fetchText_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/' + meta.html_file);
      html = sanitizePostHtml_(html);
      bloggerCall_('PATCH', '/blogs/' + cfg('BLOG_ID') + '/posts/' + post.post_id, {
        title: meta.title || post.title,
        content: html,
        labels: (meta.labels || []).filter(Boolean)
      });
      updated++;
      Logger.log('UPDATED PRODUCT LINKS: ' + (meta.title || post.title));
      Utilities.sleep(1000);
    });

    Logger.log('Product-link sync complete. Updated=' + updated + ', skipped=' + skipped);
  } finally {
    lock.releaseLock();
  }
}

/** Full status report. */
function status() {
  var pub = getPublished_();
  Logger.log('=== AutoBlog status ===');
  Logger.log('Published total : ' + pub.length);
  Logger.log('Published today : ' + countPublishedToday_() + ' / ' + cfg('MAX_PER_DAY'));
  Logger.log('Failures        : ' + (P.getProperty('FAIL_COUNT') || '0'));
  Logger.log('Breaker         : ' + (breakerOpen_() ? 'OPEN (paused)' : 'closed'));
  Logger.log('Posting hours   : ' + cfg('POST_HOURS') + ' ' + cfg('TZ'));
  Logger.log('Paid lookup     : ' + (String(cfg('PAID_LOOKUP_ENABLED', 'false')).toLowerCase() === 'true' ? 'ENABLED' : 'DISABLED'));
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
