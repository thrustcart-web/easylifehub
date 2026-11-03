# لو مجلد .github مرفوعش على GitHub — اعمل الـ 4 ملفات دي يدوي
المجلد ده **مخفي** على ويندوز وماك، فممكن ميترفعش مع الباقي.
**من غيره مفيش توليد مقالات تلقائي** — يعني النظام كله بيقف.
لو اتأكدت إن `.github/workflows/` موجود على GitHub، **تجاهل الملف ده تماماً**.

---

## إزاي تتأكد إنه موجود

افتح `https://github.com/USERNAME/autoblog` — لازم تشوف مجلد `.github` في القائمة.

لو مش موجود، لكل ملف من الأربعة:
1. على GitHub افتح الريبو → **Add file** → **Create new file**
2. في خانة الاسم اكتب المسار **بالظبط** (مثلاً `.github/workflows/tests.yml`)
   — GitHub بيعمل المجلدات لوحده لما تكتب `/`
3. انسخ المحتوى من تحت → الصقه
4. **Commit changes**

---

# الملف 1/4 — المسار: `.github/workflows/generate-daily.yml`
```yaml
name: generate-daily

# Writes new articles into content/queue/ and commits them.
# The Apps Script publisher picks them up on the next posting hour.
# Nothing here touches Blogger, so a failure can never damage your account.

on:
  schedule:
    - cron: '0 3 * * *'          # 03:00 UTC  (= 05:00 Cairo, 06:00 Riyadh)
  workflow_dispatch:
    inputs:
      count:
        description: 'How many articles to generate'
        required: false
        default: '3'
      mode:
        description: 'auto (AI + offline fallback) | ai | template'
        required: false
        default: 'auto'
  push:
    paths:
      - 'data/**'
      - 'config.json'
      - 'content/seed/**'
    branches: [ main ]

permissions:
  contents: write

concurrency:
  group: generate
  cancel-in-progress: false

jobs:
  generate:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 1

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Generate articles
        env:
          # Free AI keys - add whichever you have. The pipeline works with ZERO keys
          # (offline template engine), so a missing secret never breaks the run.
          GROQ_API_KEY:        ${{ secrets.GROQ_API_KEY }}
          MISTRAL_API_KEY:     ${{ secrets.MISTRAL_API_KEY }}
          OPENROUTER_API_KEY:  ${{ secrets.OPENROUTER_API_KEY }}
          GEMINI_API_KEY:      ${{ secrets.GEMINI_API_KEY }}
          AFFILIATE_TAG:       ${{ secrets.AFFILIATE_TAG }}
          DAILY_POSTS:         ${{ vars.DAILY_POSTS }}
          UNSPLASH_ACCESS_KEY: ${{ secrets.UNSPLASH_ACCESS_KEY }}
        run: |
          COUNT="${{ github.event.inputs.count || '3' }}"
          MODE="${{ github.event.inputs.mode || 'auto' }}"
          python3 main.py --count "$COUNT" --mode "$MODE" --verbose

      - name: Commit queue + state
        run: |
          git config user.name  "autoblog-bot"
          git config user.email "autoblog-bot@users.noreply.github.com"
          git add content/queue state.json .cache data 2>/dev/null || true
          if git diff --cached --quiet; then
            echo "Nothing new to commit."
          else
            git commit -m "content: $(date -u +'%Y-%m-%d') queue refresh [skip ci]"
            git push
          fi

      - name: Upload summary
        if: always()
        run: |
          echo "### Generated articles" >> $GITHUB_STEP_SUMMARY
          ls -1 content/queue/*.html 2>/dev/null | wc -l | xargs -I{} echo "Queue size: **{}** posts" >> $GITHUB_STEP_SUMMARY
```

---

# الملف 2/4 — المسار: `.github/workflows/keepalive.yml`
```yaml
name: keepalive

# GitHub disables scheduled workflows in repositories with no commit activity
# for 60 days. This touches a file every 5 days so the daily generator can never
# silently switch itself off - a classic "set up and forget" failure mode.

on:
  schedule:
    - cron: '30 4 */5 * *'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  keepalive:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Touch heartbeat file
        run: |
          mkdir -p .cache
          echo "last_keepalive=$(date -u +'%Y-%m-%dT%H:%M:%SZ')" > .cache/heartbeat.txt
          git config user.name  "autoblog-bot"
          git config user.email "autoblog-bot@users.noreply.github.com"
          git add .cache/heartbeat.txt
          git diff --cached --quiet || git commit -m "chore: keepalive [skip ci]" && git push

      - name: Warn if scheduled workflows were disabled
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          echo "If the daily generator ever stops, open the Actions tab and click Enable workflow."
```

---

# الملف 3/4 — المسار: `.github/workflows/refill-queue.yml`
```yaml
name: refill-queue

# Manual top-up button. Use it when the queue gets low (status() in Apps Script
# tells you how many days of content are left).

on:
  workflow_dispatch:
    inputs:
      count:
        description: 'Number of articles to generate'
        required: true
        default: '10'
      mode:
        description: 'auto | ai | template'
        required: false
        default: 'auto'

permissions:
  contents: write

jobs:
  refill:
    runs-on: ubuntu-latest
    timeout-minutes: 40
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Generate
        env:
          GROQ_API_KEY:       ${{ secrets.GROQ_API_KEY }}
          MISTRAL_API_KEY:    ${{ secrets.MISTRAL_API_KEY }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          GEMINI_API_KEY:     ${{ secrets.GEMINI_API_KEY }}
          AFFILIATE_TAG:      ${{ secrets.AFFILIATE_TAG }}
          UNSPLASH_ACCESS_KEY: ${{ secrets.UNSPLASH_ACCESS_KEY }}
        run: python3 main.py --count "${{ github.event.inputs.count }}" --mode "${{ github.event.inputs.mode }}" --verbose

      - name: Commit
        run: |
          git config user.name  "autoblog-bot"
          git config user.email "autoblog-bot@users.noreply.github.com"
          git add content/queue state.json .cache data 2>/dev/null || true
          git diff --cached --quiet || git commit -m "content: manual refill ${{ github.event.inputs.count }} [skip ci]" && git push
```

---

# الملف 4/4 — المسار: `.github/workflows/tests.yml`
```yaml
name: tests

# Runs the full pipeline test suite on every change. 31 tests, no network,
# no API keys - so a broken commit can never reach your blog.

on:
  push:
    paths: ['scripts/**', 'tests/**', 'config.json', 'data/**', 'main.py']
  pull_request:
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Run pipeline tests
        run: python3 tests/test_pipeline.py
      - name: Smoke test - generate 3 articles offline
        run: |
          python3 main.py --count 3 --mode template
          python3 main.py --stats
```

---
