# إزاي تنقل المشروع لجهازك أو لـ GitHub

المشروع فيه **36 ملف** (511 KB). رفعم واحد واحد من المتصفح هيكون ممل، فعندك **3 طرق** — اختار واحدة.

---

## 🥇 الطريقة الأولى: ملف واحد يعمل كل حاجة لوحده (من غير كمبيوتر)

**الملف:** `AutoBlog-Installer.gs` (703 KB)

ده ملف Apps Script **جواه المشروع كله** (36 ملف متشفّرين base64). بينشئ الريبو على GitHub
ويرفع كل الملفات **لوحده**. مش محتاج ZIP ولا فك ضغط ولا كمبيوتر — **شغال من الموبايل كمان**.

### الخطوات

1. **اعمل GitHub Token** (مرة واحدة):
   - **https://github.com/settings/tokens** → **Generate new token (classic)**
   - الاسم: `autoblog` · المدة: 90 يوم · **الصلاحيات: علّم على `repo` بس**
   - **Generate** → 📋 **انسخ التوكن** (مش هيظهر تاني)

2. **افتح** **https://script.google.com** → **New Project**

3. **الصق محتوى `AutoBlog-Installer.gs` كله**:
   - افتح الملف → **Ctrl+A** → **Ctrl+C**
   - في محرر Apps Script: امسح الكود الموجود → **Ctrl+V**
   - 💡 الملف كبير (703 KB) — لو المتصفح تقّل، استنى ثواني بعد اللصق قبل ما تحفظ

4. **عدّل الـ 3 قيم في أول الملف**:
   ```javascript
   var CONFIG = {
     GITHUB_TOKEN:  'ghp_xxxxxxxxxxxx',      ← التوكن
     GITHUB_USER:   'اسمك_على_github',        ← من github.com/settings/profile
     REPO_NAME:     'autoblog'
   };
   ```

5. **احفظ** (Ctrl+S) — لو سألك اسم المشروع سمّيه `AutoBlog`

6. من القائمة فوق اختار **`install`** → **Run** ▶
   - أول مرة: **Review permissions** → حسابك → **Advanced** →
     **Go to AutoBlog (unsafe)** → **Allow**
     *(طبيعي تماماً — السكريبت من تأليفك مش من Google)*
   - استنى 2–4 دقايق (بيرفع 36 ملف مع مراعاة rate limit)

7. **Executions** ← شوف اللوج. لازم تشوف:
   ```
   Uploaded: 36 / 36 files
   Repo: https://github.com/USERNAME/autoblog
   ```

8. **تأكد:** شغّل **`verifyInstall`** → لازم يقول `All 36 files verified on GitHub.`

9. **كمّل الإعداد العادي** من `docs/SETUP_AR.md` من **الخطوة 1**
   (البلوج + Google Cloud + Service Account) — دي مش ممكن تتعمل غير يدوي.

> ⚠️ **بعد ما تخلّص:** امسح `GITHUB_TOKEN` من الملف واعمل **Revoke** للتوكن من
> github.com/settings/tokens. مفيش داعي يفضل شغال.

---

## 🥈 الطريقة الثانية: ZIP + رفع على GitHub

**الملف:** `AutoBlog-Project.zip` (140 KB · 36 ملف)

1. **نزّل الـ ZIP** (من معاينة الملف → Download).
2. **فك الضغط**:
   - **ويندوز:** كليك يمين → **Extract All**
   - **ماك:** دبل كليك
   - **موبايل:** تطبيق Files / ZArchiver
3. **اعمل ريبو فاضي:** https://github.com/new → اسم `autoblog` → **Create**
   - ⚠️ **متعلّمش** على "Add a README" — سيبه فاضي تماماً
4. في صفحة الريبو الفاضية: **uploading an existing file**
5. **ادخل جوه المجلد المفكوك** واختار **كل اللي جواه** (مش المجلد نفسه):
   - `.github` · `appsscript` · `content` · `data` · `docs` · `scripts` · `tests`
   - `config.json` · `main.py` · `preview_server.py` · `README_AR.md` · `requirements.txt` · `.gitignore` · `state.json`
6. **Commit changes**

> 💡 **مجلد `.github` مخفي** على ويندوز. لو مش شايفه: في File Explorer →
> **View** → علّم على **Hidden items**.
>
> ⚠️ **لازم `.github/workflows/` يترفع** — ده اللي فيه الجدولة اليومية. من غيره
> مفيش توليد مقالات تلقائي.
>
> 📝 لو GitHub رفض ملف معين (`.gitignore` أحياناً)، ارفعه لوحده في commit تاني.

---

## 🥉 الطريقة الثالثة: Git من جهازك (لو عندك Git)

```bash
# 1) نزّل الـ ZIP وفك ضغطه، ثم:
cd AutoBlog-project

# 2) اربطه بريبو فاضي على GitHub
git init
git add .
git commit -m "AutoBlog initial"
git branch -M main
git remote add origin https://github.com/USERNAME/autoblog.git
git push -u origin main
```

---

## 📁 إيه اللي جوه الـ ZIP؟

```
36 ملف · 511 KB مفكوك · 140 KB مضغوط

.github/workflows/     4 ملفات   ← الجدولة اليومية + الاختبارات + keepalive
appsscript/Code.gs     1 ملف     ← المُجدوِل (تنسخه في Apps Script)
content/seed/          6 ملفات   ← 6 مقالات مكتوبة بإيد (هتتنشر الأول)
content/queue/         فاضي      ← النظام هيملاه لوحده
data/                  2 ملف     ← 2561 موضوع + جدول المنتجات
docs/                  2 ملف     ← دليل الإعداد + تصوّر الأوتوميشن
scripts/               12 ملف    ← المحرك كله
tests/                 1 ملف     ← 31 اختبار
config.json            ← كل الإعدادات
main.py                ← نقطة الدخول
preview_server.py      ← المعاينة المحلية
```

> ℹ️ **`content/queue/` فاضي في الـ ZIP عن قصد** — الطابور بيتولّد على GitHub
> في أول تشغيل للـ Actions (أو لما تشغّل `install` هتلاقي 6 مقالات البذرة جاهزة).
> ده بيخلّي الـ ZIP صغير (140 KB بدل 1.1 MB).

---

## بعد ما الملفات توصل GitHub

كمّل من **`docs/SETUP_AR.md`** — الخطوة 6 (مفاتيح AI) وبعدها.

**تشيك سريع إن كل حاجة وصلت:**
1. `https://github.com/USERNAME/autoblog` → لازم تشوف المجلدات السبع
2. تاب **Actions** → لو فيه شريط أصفر: **I understand my workflows, go ahead and enable them**
3. **generate-daily** → **Run workflow** → `count = 6` → **Run**
4. بعد دقيقتين → `content/queue/` لازم يبقى فيه ملفات ✅

---

## محتار؟

**لو عندك كمبيوتر:** الطريقة الثانية (ZIP) — أبسط وأأمن، مفيش توكنات.
**لو على موبايل أو مش عايز تحمل حاجة:** الطريقة الأولى (Installer).
