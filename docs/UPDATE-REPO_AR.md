# تحديث الريبو — الطريقة الصح (بعد ما طريقة الـ ZIP فشلت)

> **⚠️ مهم:** رفع ملف ZIP على GitHub عن طريق `Add file → Upload files`
> **مبيفكّش الضغط**. GitHub بيخزّنه **كملف واحد** جوه الريبو.
> ده حصل فعلًا — الملف `easylifehub-GitHub-Upload.zip` بقى جوه الريبو نفسه (252 KB)
> ومفيش ولا ملف من جوه وصل.
>
> الملف ده بيشرح الطريقة اللي **بتشتغل فعلًا**.

---

## 🥇 الطريقة الأولى: `github.dev` (الأسهل والأضمن) — 5 دقايق

`github.dev` ده **محرر VS Code كامل جوه المتصفح**. بيفتح بضغطة حرف واحد،
وبتقدر تسحب مجلدات كاملة جواه وهو بيحافظ على الهيكل (بما فيه `.github` المخفي).

### 1) افتح المحرر

روح على:
```
https://github.com/thrustcart-web/easylifehub
```

ودوس حرف **`.`** (النقطة) في الكيبورد — **حرف واحد بس**.

> أو يدوي: غيّر الرابط لـ `https://github.dev/thrustcart-web/easylifehub`

هيفتح محرر شكله زي VS Code، وعلى الشمال **EXPLORER** فيه كل ملفات الريبو.

### 2) فكّ الضغط على جهازك

فكّ `easylifehub-GitHub-Upload.zip` على جهازك.

> **Windows:** كليك يمين على الـ ZIP → **Extract All** → Extract
> **Mac:** دبل كليك على الـ ZIP

هيوصلك **مجلد** اسمه `easylifehub-GitHub-Upload` (أو شبهه) — **ادخل جواه**.
اللي جوه المفروض يكون كده:

```
00-اقرا-ده-اول.md
.gitignore
README_AR.md
WORKFLOWS-FALLBACK_AR.md
main.py
requirements.txt
config.json
appsscript/
content/
data/
docs/
scripts/
tests/
tools/
.github/          ← مخفي!
```

> **لو مش شايف `.github` و `.gitignore`:**
> - **Windows:** File Explorer → **View** → **Show** → ✅ **Hidden items**
> - **Mac:** في Finder → **Cmd + Shift + .** (نقطة)

### 3) اسحب المجلدات والملفات جوه المحرر

من الـ File Explorer على جهازك، **اسحب** الحاجات دي وسيبها في منطقة **EXPLORER** في `github.dev`:

```
.github          appsscript       content          data
docs             scripts          tests            tools
.gitignore       00-اقرا-ده-اول.md   README_AR.md    WORKFLOWS-FALLBACK_AR.md
config.json      main.py          requirements.txt
```

> **اسحبها من جوه المجلد المفكوك** — مش المجلد نفسه.
> لو سحبت المجلد كله هيعمل `easylifehub-GitHub-Upload/...` وده غلط.

لما يسألك **"Replace / Overwrite?"** → اضغط **Replace** (أو Yes to all).

### 4) ⚠️ حاجتين لازم تعملهم يدوي

**(أ) احذف الـ ZIP من الريبو**

في EXPLORER: كليك يمين على `easylifehub-GitHub-Upload.zip` → **Delete**

**(ب) متلمسش `state.json`**

`state.json` فيه **تقدم مدونتك الحقيقي** (إيه اللي اتولّد وإيه اللي اتنشر).
الـ ZIP **مفيهوش** `state.json` بالظبط عشان كده — فمش هيحصل تعارض.
بس **لو شوفته في قائمة الملفات اللي بتسحبها، شيله منها.**

### 5) تأكد من الهيكل

في EXPLORER لازم تشوف:

```
✅ .github/workflows/generate-daily.yml
✅ .github/workflows/refill-queue.yml
✅ .github/workflows/keepalive.yml
✅ .github/workflows/tests.yml
✅ scripts/categories.py          ← جديد
✅ tools/expand_keywords.py       ← جديد
✅ data/ramp.json                 ← جديد
✅ data/keywords.csv              ← حجمه يكبر لـ 615 KB
✅ docs/GROWTH_AR.md              ← جديد
❌ easylifehub-GitHub-Upload.zip  ← لازم يكون اتحذف
```

### 6) اعمل Commit

- على الشمال، اضغط أيقونة **Source Control** (شكلها تفريعة، تحت EXPLORER)
- هتشوف قائمة بكل التغييرات (حوالي 28 ملف)
- في خانة **Message** اكتب:
  ```
  update: growth ramp + 28 categories + 7135 topics
  ```
- اضغط **✓ Commit & Push** (أو `Commit` بعدين `Push`)

### 7) تأكد

افتح:
```
https://github.com/thrustcart-web/easylifehub
```

لازم تشوف في القائمة: `.github` · `appsscript` · `content` · `data` · `docs` · `scripts` · `tests` · `tools`

**ومش لازم تشوف:** `easylifehub-GitHub-Upload.zip`

---

## 🥈 الطريقة التانية: الـ Installer (من غير ما ترفع ملف واحد)

لو `github.dev` مزعجك، فيه ملف واحد بيرفع **كل حاجة لوحده** عن طريق GitHub API.

### 1) اعمل GitHub Token

1. افتح: `https://github.com/settings/tokens`
2. اضغط **Generate new token** → **Generate new token (classic)**
3. **Note:** اكتب `autoblog`
4. **Expiration:** اختار `90 days` (كفاية)
5. **Select scopes:** علّم على ✅ **repo** (أول مربع — علّم عليه هو بس، كل اللي تحته بيتعلّم تلقائي)
6. انزل لتحت → **Generate token**
7. **انسخ التوكن فورًا** (هيظهر مرة واحدة بس!) — شكله `ghp_xxxxxxxxxxxx...`

### 2) افتح Apps Script

1. `https://script.google.com` → **New project**
2. امسح اللي في الملف كله
3. افتح `AutoBlog-Installer.gs` → **Ctrl+A** → **Ctrl+C** → الصق في Apps Script
4. **Ctrl+S** (Save)

> الملف حجمه 1.8 MB. اللصق ممكن ياخد **10–20 ثانية**. استنى لحد ما يخلص.

### 3) حط التوكن

في أول الملف فيه `CONFIG`:

```javascript
var CONFIG = {
  GITHUB_TOKEN:  'PUT_YOUR_GITHUB_PAT_HERE',   ← غيّر ده بس
  GITHUB_USER:   'thrustcart-web',              ← معبّى جاهز
  REPO_NAME:     'easylifehub',                 ← معبّى جاهز
  BLOG_ID:       '6831073772272108266',         ← معبّى جاهز
  BLOG_URL:      'https://easylifehub7083.blogspot.com',
  AFFILIATE_TAG: 'qalok0c-20',
  NOTIFY_EMAIL:  'thrustcart@gmail.com',
  ...
};
```

**اللي محتاج تعدّله: `GITHUB_TOKEN` بس.** كل الباقي معبّى بقيمك.

### 4) شغّل

من فوق: اختار الدالة من القائمة المنسدلة → اضغط **Run**

| بالترتيب | الدالة | تعمل إيه |
|---|---|---|
| 1 | `install()` | يرفع 44 ملف للريبو (بياخد ~3 دقايق) |
| 2 | `verifyInstall()` | يتأكد إن كل الملفات وصلت → `OK - all 44 files verified` |
| 3 | `setupPublisher()` | يكتب الـ Script Properties |

> **مهم:** `install()` **مش بيكتب `state.json`** — عشان يحافظ على تقدم مدونتك.

### 5) امسح الـ ZIP يدوي

الـ Installer مش بيحذف ملفات. فامسح `easylifehub-GitHub-Upload.zip` من الريبو:
```
https://github.com/thrustcart-web/easylifehub/blob/main/easylifehub-GitHub-Upload.zip
```
→ 🗑️ (أيقونة السلة) → **Commit changes**

---

## 🥉 الطريقة التالتة: ملف ملف (لو الاتنين فوق مش راضيين)

`github.com/thrustcart-web/easylifehub` → **Add file** → **Create new file**

في خانة الاسم اكتب المسار كامل بالشرطة المائلة، وGitHub هيعمل المجلد لوحده:

```
scripts/categories.py
```

الصق المحتوى → **Commit changes**. كرّر لكل ملف.

**الملفات المطلوبة (28 ملف):**

```
.gitignore
config.json
main.py
appsscript/Code.gs
data/keywords.csv            ← 615 KB، ممكن ياخد وقت
data/ramp.json
scripts/affiliate.py
scripts/ai_writer.py
scripts/blogger.py
scripts/categories.py
scripts/generate.py
scripts/images.py
scripts/keywords.py
scripts/providers.py
scripts/render.py
scripts/safety.py
scripts/template_engine.py
scripts/topic_planner.py     ← 50 KB
scripts/util.py
tests/test_pipeline.py
tools/expand_keywords.py
docs/GITHUB_AR.md
docs/GROWTH_AR.md
docs/UPDATE-REPO_AR.md
.github/workflows/generate-daily.yml
.github/workflows/refill-queue.yml
.github/workflows/keepalive.yml
.github/workflows/tests.yml
```

> ⚠️ **متعملش ده لـ `state.json`** — سيبه زي ما هو.

---

## بعد ما تحدّث: اختبر

### 1) الـ tests

```
https://github.com/thrustcart-web/easylifehub/actions/workflows/tests.yml
```
→ **Run workflow** → **Run workflow**
→ لازم يخلص أخضر ✅ (**37 اختبار**)

### 2) توليد مقالات

```
https://github.com/thrustcart-web/easylifehub/actions/workflows/generate-daily.yml
```
→ **Run workflow**
→ **`count`: سيبه فاضي** ← مهم! عشان الـ ramp يشتغل
→ **`mode`: `auto`**
→ **Run workflow**

لازم تشوف في اللوج:

```
Ramp: day 0 -> 3 post(s)/day (hard cap 6)
categories: 28 niches, 7,135 planned topics
```

### 3) صفحة الأقسام اتبنت؟

```
https://github.com/thrustcart-web/easylifehub/tree/main/content/categories
```
→ المفروض `index.html` و `index.json`

---

## 🔴 وفي الإعدادات

> **Settings → Secrets and variables → Actions → Variables → احذف `DAILY_POSTS`**

وجودها **بيجمّد العدد على 3 للأبد** ويلغي خطة النمو المتدرج.

---

## الأسئلة المتوقعة

**س: الـ 9 مقالات اللي في `content/queue` هتتحذف؟**
ج: **لأ.** محدش هيلمسها. هتتنشر عادي. المقالات الجديدة هتتحط جنبهم.

**س: `state.json` هيتأثر؟**
ج: **لأ.** مقصودًا مستثنينه من الـ ZIP ومن الـ Installer.

**س: `.cache/images.json`؟**
ج: سيبه. ده كاش الصور، مفيش مشكلة.

**س: ليه الـ ZIP لسه موجود في الملف اللي نزلته؟**
ج: لأنه الطريقة الوحيدة تنقل بيها 43 ملف مرة واحدة. بس **مترفعوش على GitHub** —
فكّه على جهازك واستخدم `github.dev`. واحذف النسخة اللي في الريبو.

**س: ممكن أرفع الملفات واحدة واحدة بـ `Add file → Upload files`؟**
ج: **لأ** — الطريقة دي بترفع كل الملفات **في جذر الريبو**، فمش هتعمل مجلدات.
لازم تستخدم `github.dev` أو `Create new file` بالمسار الكامل.
