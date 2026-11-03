# تحديث الريبو — خطوة بخطوة (أكتوبر/نوفمبر 2026)

> التحديث ده بيضيف: **خطة النمو المتدرج (3→6)** + **7,135 موضوع في 28 قسم**
> + **صفحة أقسام** + **روابط داخلية حية** + إصلاحات جودة مهمة.
>
> الوقت المطلوب: **~10 دقايق**. الصعوبة: **سهل** (رفع ملفات بس).

---

## ⚠️ الأول: حاجة واحدة لازم تعملها في الإعدادات

> **Settings → Secrets and variables → Actions → Variables**
> **احذف `DAILY_POSTS`**

ليه؟ لأن وجودها **بيجمّد العدد على 3 للأبد** ويلغي خطة النمو المتدرج.
الـ ramp هو اللي لازم يقرر العدد حسب عمر المدونة.

---

## الطريقة (أ) — الأسهل: رفع ZIP كامل ✅ (أنصح بيها)

### 1) افتح الريبو

```
https://github.com/thrustcart-web/easylifehub
```

### 2) ارفع الملف

- اضغط **Add file** (فوق، على اليمين من قائمة الملفات)
- اختار **Upload files**
- اسحب الملف ده جوه المنطقة المخططة:

```
easylifehub-GitHub-Upload.zip
```

> **مهم:** الـ ZIP ده محتوياته **في جذر الملف المضغوط مباشرة** (مفيش مجلد لفّ حوالين الملفات).
> عشان كده GitHub هيفكّه ويحط الملفات في مكانها الصح.
> لو شوفت مجلد `affiliate-blog/` ظهر في الريبو → إنت رفعت الـ ZIP الغلط.
> الملف الصح اسمه **`easylifehub-GitHub-Upload.zip`**.

### 3) استنى

هياخد **دقيقة أو اتنين** عشان `data/keywords.csv` حجمها 615 KB.
شريط تقدّم هيظهر تحت كل ملف.

### 4) اعمل Commit

في خانة **Commit changes** (تحت):
- اكتب في السطر الأول: `update: growth ramp + 28 categories + 7135 topics`
- اختار **Commit directly to the `main` branch**
- اضغط **Commit changes** (الأخضر)

### 5) تأكد إن الملفات وصلت

افتح الروابط دي واحد واحد — **لازم كلها تفتح من غير 404**:

| الرابط | المفروض تشوف |
|---|---|
| [`config.json`](https://github.com/thrustcart-web/easylifehub/blob/main/config.json) | فيه قسم `"ramp"` وقسم `"categories"` |
| [`data/keywords.csv`](https://github.com/thrustcart-web/easylifehub/blob/main/data/keywords.csv) | **7136 سطر** (الصف الأول عناوين) |
| [`scripts/categories.py`](https://github.com/thrustcart-web/easylifehub/blob/main/scripts/categories.py) | ملف جديد |
| [`tools/expand_keywords.py`](https://github.com/thrustcart-web/easylifehub/blob/main/tools/expand_keywords.py) | ملف جديد |
| [`data/ramp.json`](https://github.com/thrustcart-web/easylifehub/blob/main/data/ramp.json) | فيه `"day0"` |
| [`.github/workflows/generate-daily.yml`](https://github.com/thrustcart-web/easylifehub/blob/main/.github/workflows/generate-daily.yml) | `default: ''` تحت `count` |
| [`appsscript/Code.gs`](https://github.com/thrustcart-web/easylifehub/blob/main/appsscript/Code.gs) | فيه `syncCategoryPage` |

### 6) ⚠️ ارجّع قيمك في `config.json`

الـ ZIP فيه **قيمك بالظبط** (blog id / url / tag / repo) — فمفروض متحتاجش تعمل حاجة.
بس **اتأكد** إن السطور دي موجودة صح:

```json
"blog": {
  "id": "6831073772272108266",
  "url": "https://easylifehub7083.blogspot.com",
  "name": "Easy Life Hub"
},
"affiliate": { "tag": "qalok0c-20", ... },
"github": { "repo_raw_base": "https://raw.githubusercontent.com/thrustcart-web/easylifehub/main" },
"notify": { "email": "thrustcart@gmail.com", ... }
```

> لو حبيت تتأكد إن الـ JSON سليم: انسخه كله في **https://jsonlint.com** واضغط Validate.

---

## الطريقة (ب) — لو الـ ZIP مش راضي يرفع

ارفع الملفات **واحد واحد** من نفس الصفحة:

`Add file → Upload files` واختار الملفات دي (من الـ ZIP بعد فكّه):

```
config.json
main.py
state.json
data/keywords.csv          ← الأهم (615 KB)
data/ramp.json
scripts/categories.py      ← جديد
scripts/keywords.py
scripts/topic_planner.py
scripts/generate.py
scripts/render.py
appsscript/Code.gs
.github/workflows/generate-daily.yml
.github/workflows/refill-queue.yml
docs/GROWTH_AR.md          ← جديد
```

> **تنبيه:** مجلد `.github` مخفي. لو مش شايفه بعد فك الـ ZIP:
> - **Windows:** في File Explorer → View → Show → **Hidden items** ✅
> - **Mac:** في Finder → **Cmd + Shift + .** (نقطة)

---

## الطريقة (ج) — من غير GitHub خالص: الـ Installer

لو الطرق دي مزعجاك، فيه ملف واحد بيعمل كل حاجة لوحده:

```
AutoBlog-Installer.gs
```

1. افتح **https://script.google.com** → **New project**
2. امسح اللي في الملف والصق `AutoBlog-Installer.gs` كله → **Save**
3. في `CONFIG` فوق: حط **`GITHUB_TOKEN`** بس (كل القيم التانية معبّاة جاهزة باسمك)
   - التوكن من: `github.com/settings/tokens` → **Generate new token (classic)** → صلاحية **repo**
4. شغّل **`install()`** → هيرفع كل الملفات للريبو لوحده
5. شغّل **`verifyInstall()`** → لازم يقول `OK - all 42 files verified`
6. شغّل **`setupPublisher()`** → يكتب الـ Script Properties
7. حط **`SA_JSON`** في Project Settings → Script Properties
8. شغّل **`testConnection()`** → لازم يطبع `OK` في كل السطور
9. شغّل **`installTrigger()`** → **خلصنا، النظام شغال لوحده**

> ⚠️ الملف حجمه **1.4 MB**. لو Apps Script اتلعثم في اللصق، استنى شوية أو استخدم الطريقة (أ).

---

## بعد ما ترفع: اختبر

### 1) شغّل الـ tests

```
https://github.com/thrustcart-web/easylifehub/actions/workflows/tests.yml
```
→ **Run workflow** → **Run workflow**
→ لازم يخلص أخضر ✅ (33 اختبار)

### 2) شغّل توليد مقالات

```
https://github.com/thrustcart-web/easylifehub/actions/workflows/generate-daily.yml
```
→ **Run workflow**
→ **count: سيبه فاضي** (عشان الـ ramp يشتغل)
→ **mode: `auto`**
→ **Run workflow**

### 3) شوف النتيجة في اللوج

لازم تشوف السطر ده:

```
Ramp: day 0 -> 3 post(s)/day (hard cap 6)
```

ولو `day 0` → العدد 3. بعد 10 أيام هيبقى 4 لوحده. بعد 30 → 5. بعد 60 → 6.

### 4) تأكد إن صفحة الأقسام اتبنت

```
https://github.com/thrustcart-web/easylifehub/tree/main/content/categories
```
→ المفروض تلاقي `index.html` و `index.json`

---

## الأسئلة المتوقعة

**س: الـ refill-queue اللي كان شغال وقع بـ timeout. مشكلة؟**
ج: لأ. الملفات بتتحفظ في **آخر خطوة** بس، فالطابور فضل زي ما هو.
ومش محتاج refill أصلاً — الـ cron اليومي بيولّد لوحده.
ولو حبيت refill، استخدم `count = 6` (مش 20) عشان يخلّص في ~12 دقيقة.

**س: المقالات الـ 6 القديمة (seed-*) في الطابور؟**
ج: هتتنشر الأول وبعدين المقالات الجديدة. تقدر تسيبها أو تمسحها، مفيش فرق كبير.

**س: محتاج أعمل refill كل شوية؟**
ج: **لأ.** الـ `generate-daily` بيشتغل كل يوم لوحده وبيضيف مقالات.
الـ refill اختياري تمامًا — بس لو عايز مخزون أكبر مقدمًا.

**س: إزاي أعرف إنت في أنهي مرحلة من الـ ramp؟**
ج: في ملخص أي run في Actions هيظهر لك الـ JSON بتاع `--ramp`.
أو افتح `data/ramp.json` وشوف `day0`.

---

## اللي اتغير بالظبط (للمرجع)

| الملف | التغيير |
|---|---|
| `config.json` | + قسم `ramp` (المراحل) + قسم `categories` |
| `data/keywords.csv` | 2,561 → **7,135** صف، 18 → **28** قسم |
| `data/ramp.json` | **جديد** — تاريخ بداية العد |
| `scripts/categories.py` | **جديد** — صفحة الأقسام + شارات الأقسام |
| `scripts/keywords.py` | أنماط جديدة + `clean_search_term()` + حماية أرقام الموديلات |
| `scripts/topic_planner.py` | محرك عناوين جديد + `_article()` متظبط + اختيار variants حسب النوع |
| `scripts/generate.py` | الـ ramp + بناء صفحة الأقسام + `--ramp` |
| `scripts/render.py` | شارة القسم + مكان الروابط ذات الصلة + metadata |
| `tools/expand_keywords.py` | **جديد** — مولّد المواضيع |
| `appsscript/Code.gs` | `syncCategoryPage()` + `appendRelated_()` + `absolutizeLinks_()` |
| `.github/workflows/*.yml` | مبقاش يبعت `--count` ثابت + بيضم `content/categories` |
| `docs/GROWTH_AR.md` | **جديد** — شرح خطة النمو كلها |
