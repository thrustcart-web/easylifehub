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
  var cached = P.getProperty('TOKEN');
  var exp = Number(P.getProperty('TOKEN_EXP') || 0);
  if (cached && exp > new Date().getTime() + 60000) return cached;

  var sa = JSON.parse(cfg('SA_JSON'));
  if (!sa.client_email || !sa.private_key) throw new Error('SA_JSON is not a valid service account key');

  var now = Math.floor(new Date().getTime() / 1000);
  var header = { alg: 'RS256', typ: 'JWT' };
  var claim = {
    iss: sa.client_email,
    scope: 'https://www.googleapis.com/auth/blogger',
    aud: 'https://oauth2.googleapis.com/token',
    iat: now,
    exp: now + 3600
  };
  var unsigned = b64url_(JSON.stringify(header)) + '.' + b64url_(JSON.stringify(claim));
  var signature = Utilities.computeRsaSha256Signature(unsigned, sa.private_key);
  var jwt = unsigned + '.' + b64urlBytes_(signature);

  var res = UrlFetchApp.fetch('https://oauth2.googleapis.com/token', {
    method: 'post',
    payload: {
      grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
      assertion: jwt
    },
    muteHttpExceptions: true
  });
  var body = JSON.parse(res.getContentText());
  if (!body.access_token) throw new Error('Token request failed: ' + res.getContentText().slice(0, 300));

  P.setProperty('TOKEN', body.access_token);
  P.setProperty('TOKEN_EXP', String(new Date().getTime() + (body.expires_in - 60) * 1000));
  return body.access_token;
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
  if (code < 200 || code >= 300) throw new Error('Blogger API ' + code + ': ' + text.slice(0, 400));
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
  var hubPath = String(cfg('CATEGORY_HUB') || '');
  if (!hubPath) return null;
  var html;
  try {
    html = fetchText_(cfg('RAW_BASE') + '/' + hubPath);
  } catch (e) {
    Logger.log('category hub not published yet (' + e + ')');
    return null;
  }
  if (!html || html.indexOf('404') === 0) { Logger.log('category hub: file not found'); return null; }
  html = absolutizeLinks_(html);

  var title = String(cfg('CATEGORY_PAGE') || 'Browse All Categories');
  var blogId = cfg('BLOG_ID');
  var pages = [];
  try {
    pages = (bloggerCall_('GET', '/blogs/' + blogId + '/pages', { 'maxResults': 150 }).items) || [];
  } catch (e) {
    Logger.log('page list failed: ' + e);
    return null;
  }
  var existing = null;
  pages.forEach(function (pg) {
    if (!existing && String(pg.title || '').toLowerCase() === title.toLowerCase()) existing = pg;
  });
  try {
    if (existing) {
      var out = bloggerCall_('PUT', '/blogs/' + blogId + '/pages/' + existing.id,
                             { title: title, content: html });
      Logger.log('Category page updated: ' + out.url);
      return out;
    }
    var created = bloggerCall_('POST', '/blogs/' + blogId + '/pages',
                               { title: title, content: html });
    Logger.log('Category page created: ' + created.url);
    Logger.log('Add it to your menu: Blogger -> Layout -> Pages gadget -> ' + title);
    return created;
  } catch (e) {
    Logger.log('category page failed: ' + e);
    return null;
  }
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
  var to = cfg('NOTIFY_EMAIL') || Session.getActiveUser().getEmail();
  if (!to) return;
  try { MailApp.sendEmail(to, '[AutoBlog] ' + subject, body); } catch (e) { Logger.log('notify failed: ' + e); }
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
    if (countPublishedToday_() >= maxPerDay) break;

    var html = fetchText_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/' + meta.html_file);
    var labels = (meta.labels || []).filter(Boolean);

    html = sanitizePostHtml_(html);
    var post = insertPost_(meta.title, html, labels);
    // bonuses - never allowed to break the publish itself
    try { syncCategoryPage(); } catch (e) { Logger.log('hub: ' + e); }

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
/** Creates the hourly trigger (run once). */
function installTrigger() {
  var existing = ScriptApp.getProjectTriggers();
  existing.forEach(function (t) {
    if (t.getHandlerFunction() === 'publishScheduled') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('publishScheduled').timeBased().everyHours(1).create();
  Logger.log('Hourly trigger installed. Posting hours: ' + cfg('POST_HOURS') + ' (' + cfg('TZ') + ')');
}

/** Connectivity + permission check (run once after setup). */
function testConnection() {
  try {
    var blog = bloggerCall_('GET', '/blogs/' + cfg('BLOG_ID'));
    Logger.log('OK  blog name : ' + blog.name);
    Logger.log('OK  blog url  : ' + blog.url);
    Logger.log('OK  account   : ' + JSON.parse(cfg('SA_JSON')).client_email);
    var index = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/index.json');
    Logger.log('OK  queue     : ' + (index.count || 0) + ' articles waiting');
    Logger.log('OK  published : ' + getPublished_().length + ' so far');
    Logger.log('OK  blog url  : ' + (blogUrl_() || '(unknown)'));
    try { syncCategoryPage(); } catch (e) { Logger.log('WARN category page: ' + e); }
    Logger.log('');
    Logger.log('Everything works. Run installTrigger() if you have not, then forget about it.');
  } catch (err) {
    Logger.log('FAILED: ' + err);
    Logger.log('');
    Logger.log('Checklist:');
    Logger.log(' 1. SA_JSON contains the FULL service-account JSON (including private_key)');
    Logger.log(' 2. Blogger API v3 is enabled in that Google Cloud project');
    Logger.log(' 3. The service-account email was added as an AUTHOR on the blog:');
    Logger.log('    Blogger -> Settings -> Permissions -> Invite more users');
    Logger.log(' 4. BLOG_ID is correct (from the blog URL: blogger.com/blog/posts/1234567890)');
    Logger.log(' 5. RAW_BASE points at a PUBLIC repo raw URL, branch included');
  }
}



/**
 * Create a Blogger draft from the next pending article with layout_version 2.
 * Does not mark the item published, modify the queue or publish live.
 */
function createQueuePreviewDraft() {
  var index = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/index.json');
  var posts = index.posts || [];
  var candidate = null;

  for (var i = 0; i < posts.length; i++) {
    var item = posts[i];
    if (item && item.slug && !isPublished_(item.slug) && Number(item.layout_version || 0) >= 2) {
      candidate = item;
      break;
    }
  }

  if (!candidate) {
    throw new Error('No pending layout-v2 article found. Run the GitHub generation workflow after the update, then run this function again.');
  }

  var html = fetchText_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/' + candidate.html_file);
  html = sanitizePostHtml_(html);

  var draft = bloggerCall_('POST', '/blogs/' + cfg('BLOG_ID') + '/posts',
    { title: candidate.title, content: html, labels: (candidate.labels || []).filter(Boolean) },
    { isDraft: true });

  if (!draft || !draft.id) throw new Error('Blogger did not return a draft ID.');
  Logger.log('DRAFT CREATED: ' + candidate.title);
  Logger.log('DRAFT ID: ' + draft.id);
  Logger.log('DRAFT URL: ' + (draft.url || 'Open Blogger > Posts > Drafts'));
  Logger.log('This preview did not mark the queue item as published.');
  return { title: candidate.title, slug: candidate.slug, draftId: String(draft.id), url: draft.url || '' };
}
/** Publish immediately regardless of the hour (for testing). */
function publishNow() {
  if (breakerOpen_()) return;
  try { publishNext_(1); }
  catch (err) { recordFailure_(err); Logger.log('FAILED: ' + err); }
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
  Logger.log('Triggers        : ' + ScriptApp.getProjectTriggers().length);
  try {
    var index = fetchJson_(cfg('RAW_BASE') + '/' + cfg('QUEUE_DIR') + '/index.json');
    var pending = (index.posts || []).filter(function (p) { return !isPublished_(p.slug); }).length;
    Logger.log('Queue total     : ' + (index.count || 0));
    Logger.log('Queue pending   : ' + pending);
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
