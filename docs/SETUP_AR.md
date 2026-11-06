# دليل الإعداد الكامل — مرة واحدة (~25 دقيقة) وبعدها تنسى النظام خالص

> امشي بالترتيب. كل خطوة فيها **القيمة اللي هتنسخها** وإزاي تحصل عليها.
> لو وقفت في أي خطوة، شوف قسم [حل المشاكل](#حل-المشاكل) في الآخر.

**محتاج منك:** حساب جوجل · حساب GitHub · حساب على بلوجر · كوباية قهوة ☕

---

## خريطة الطريق

| # | الخطوة | الوقت | هتحتاجها فين |
|---|---|---|---|
| 1 | إنشاء البلوج على Blogger | 3 د | — |
| 2 | استخراج Blog ID | 1 د | Apps Script + config.json |
| 3 | مشروع Google Cloud + تفعيل Blogger API | 5 د | — |
| 4 | Service Account + تنزيل المفتاح JSON | 4 د | Apps Script |
| 5 | إضافة الـ Service Account كمؤلف في البلوج | 2 د | — |
| 6 | مفاتيح AI مجانية (Groq وأخواته) | 5 د | GitHub Secrets |
| 7 | رفع المشروع على GitHub + الأسرار | 5 د | — |
| 8 | Apps Script: لصق الكود + الإعداد + المُشغّل | 5 د | — |
| 9 | اختبار من الطرف للطرف | 2 د | — |

---

## 1) إنشاء البلوج

1. روح على **https://www.blogger.com** وسجّل دخول بحساب جوجل.
2. اضغط **New blog** / **مدونة جديدة**.
3. اختار **اسم** و**عنوان** (`yourname.blogspot.com`).
4. **مهم جداً:** من إعدادات البلوج → **Settings** → فعّل:
   - **HTTPS availability** → Enabled
   - **HTTPS redirect** → Enabled

> 💡 **نصيحة:** اسم البلوج يكون عام شوية يقدر يستوعب أكتر من نيتش
> (مثلاً "Honest Picks" أو "The Buyer's Desk") — عشان لو حبيت توسّع المجالات بعدين.

---

## 2) استخراج Blog ID

من لوحة تحكم بلوجر، بص على **شريط العنوان في المتصفح**:

```
https://www.blogger.com/blog/posts/1234567890123456789
                                          ^^^^^^^^^^^^^^^^^^^
                                          ده الـ Blog ID
```

**📋 انسخ الرقم ده** → هتحتاجه في `config.json` وفي Apps Script.

---

## 3) مشروع Google Cloud + تفعيل Blogger API

1. روح على **https://console.cloud.google.com** بنفس حساب جوجل.
2. أول مرة هتظهرلك شروط الاستخدام → وافق.
3. من الشريط اللي فوق، اضغط على اسم المشروع → **New Project** / **مشروع جديد**.
   - الاسم: `autoblog` (أي اسم ينفع)
   - اضغط **Create** واستنى ثواني، ثم **تأكد إنه مُحدَّد** في الشريط اللي فوق.
4. فعّل الـ API:
   - القائمة الجانبية → **APIs & Services** → **Library**
   - في البحث اكتب: **Blogger API**
   - افتح **Blogger API v3** → اضغط **Enable** / **تفعيل**

> ⚠️ لو نسيت الخطوة دي، كل محاولات النشر هترجع خطأ 403.

---

## 4) Service Account + تنزيل المفتاح

1. القائمة الجانبية → **IAM & Admin** → **Service Accounts**.
2. اضغط **+ Create Service Account** / **إنشاء حساب خدمة**.
   - الاسم: `blogger-publisher`
   - الوصف: أي حاجة
   - اضغط **Create and Continue**
3. في خطوة الصلاحيات: **سيبها فاضية** واضغط **Continue** ثم **Done**.

   > ℹ️ الصلاحية الحقيقية هتيجي من كونه "مؤلف" في البلوج (الخطوة الجاية)،
   > مش من صلاحيات Google Cloud.

4. اضغط على اسم الـ Service Account اللي لسه عامله.
5. تبويب **Keys** → **Add Key** → **Create new key** → اختار **JSON** → **Create**.
6. هيتنزّل ملف اسمه شبه: `autoblog-1a2b3c4d5e6f.json`

**📋 افتح الملف بـ Notepad وانسخ محتواه كله** — هتحتاجه في Apps Script.

**📋 وكمان انسخ الإيميل اللي في أول الملف** — شكله كده:
```
blogger-publisher@autoblog.iam.gserviceaccount.com
```

> 🔒 **الملف ده = مفتاح النشر.** ماترفعهوش على GitHub ولا تبعته لحد.
> الـ `.gitignore` في المشروع بيمنع رفعه تلقائياً.

---

## 5) إضافة الـ Service Account كمؤلف في البلوج

1. لوحة تحكم بلوجر → **Settings** / **الإعدادات**.
2. قسم **Permissions** / **الأذونات**.
3. اضغط **Invite more users** / **دعوة المزيد من المستخدمين**.
4. الصق **إيميل الـ Service Account** اللي نسخته في الخطوة 4.
5. اختار الدور: **Author** / **مؤلف**.
6. اضغط **Invite**.

> ✅ **مفيش إيميل تأكيد ولا قبول دعوة** — الـ Service Account بيتضاف فوراً.
> دي بالظبط الخطوة اللي بتلغي احتياجك لأي "إيميل نشر سري".

**جرّب إنها اشتغلت:** افتح `Settings → Permissions` ولازم تلاقي الإيميل مكتوب تحت **Blog authors**.

---

## 6) مفاتيح AI مجانية (من غير بطاقة بنكية)

النظام بيشتغل بسلسلة بدائل: لو واحد خلص، يجرب اللي بعده. **اعمل مفتاح واحد على الأقل**
(والبديل الأوفلاين هيشتغل لو مفيش أي مفتاح خالص).

### 🥇 Groq — الأهم (1000 طلب/يوم، مجاني للأبد)
1. **https://console.groq.com** → سجّل دخول بحساب جوجل.
2. من القائمة: **API Keys** → **Create API Key**.
3. **📋 انسخ المفتاح** (بيبدأ بـ `gsk_...`) — مش هيظهر تاني.

### 🥈 Mistral (اختياري)
1. **https://console.mistral.ai** → إنشاء حساب.
2. **API Keys** → أنشئ مفتاح → **📋 انسخه**.

### 🥉 OpenRouter (اختياري — 50 طلب/يوم)
1. **https://openrouter.ai** → تسجيل → **Keys** → **Create Key** → **📋 انسخه**.

### Gemini (اختياري — الفري تاير اتقصّ كتير مؤخراً)
1. **https://aistudio.google.com** → **Get API key** → **📋 انسخه**.

### 🖼️ Unsplash لصور الغلاف (اختياري تماماً)
1. **https://unsplash.com/developers** → **Your apps** → **New Application** → وافق على الشروط.
2. **📋 انسخ الـ Access Key**.

> 💡 **من غير المفتاح ده النظام لسه بيحط صور غلاف** — بيولّد كارت SVG أنيق محلياً.
> يعني الصور مش هتبقى مكسورة أبداً.

---

## 7) رفع المشروع على GitHub

### أ) إنشاء الريبو
1. **https://github.com/new**
2. اسم الريبو: `autoblog` (أو أي اسم).
3. **Public** أو **Private** — الاتنين شغال. (Private أفضل لو مش عايز مقالاتك تتشاف قبل النشر).

   > ⚠️ **لو Private:** لازم تعمل خطوة زيادة في الآخر عشان Apps Script يقرا الملفات —
   > شوف [ملاحظة الريبو الخاص](#ملاحظة-الريبو-الخاص).

4. اضغط **Create repository**.

### ب) رفع الملفات
في صفحة الريبو الجديدة:
1. اضغط **uploading an existing file**.
2. اسحب **كل محتويات مجلد المشروع** (ماعدا `.git`).
3. تحت اضغط **Commit changes**.

### ج) الأسرار (Secrets)
1. **Settings** (تاب الريبو) → **Secrets and variables** → **Actions**.
2. تبويب **Secrets** → **New repository secret**، وضيف:

| الاسم | القيمة |
|---|---|
| `GROQ_API_KEY` | مفتاح Groq |
| `MISTRAL_API_KEY` | مفتاح Mistral (اختياري) |
| `OPENROUTER_API_KEY` | مفتاح OpenRouter (اختياري) |
| `GEMINI_API_KEY` | مفتاح Gemini (اختياري) |
| `UNSPLASH_ACCESS_KEY` | مفتاح Unsplash (اختياري) |
| `AFFILIATE_TAG` | **تاج الأفلييت بتاعك** (مثلاً `mypicks-20`) |

> 🏷️ **التاج بتاعك فين؟** من **https://affiliate-program.amazon.com** →
> حسابك → الـ Tracking ID. شكله `something-20` لأمازون الأمريكي.

3. تبويب **Variables** → **New repository variable**:

| الاسم | القيمة |
|---|---|
| `DAILY_POSTS` | `3` |

### د) تعديل config.json على GitHub
1. افتح `config.json` في الريبو → اضغط 🖉 (**Edit**).
2. عدّل القيم دي:

```json
"blog": {
  "id": "1234567890123456789",              ← الـ Blog ID من خطوة 2
  "url": "https://yourname.blogspot.com",
  "name": "اسم مدونتك"
},
"affiliate": {
  "tag": "mypicks-20",                        ← تاجك
  "marketplace": "com"                        ← com / eg / sa / co.uk ...
},
"github": {
  "repo_raw_base": "https://raw.githubusercontent.com/USERNAME/autoblog/main"
}
```

> ⚠️ `repo_raw_base` — غيّر `USERNAME` لاسمك على GitHub، و`autoblog` لاسم الريبو.
> لو الريبو الخاص (Private)، الرابط ده **مش هيشتغل** من غير توكن — شوف الملاحظة تحت.

3. **Commit changes**.

### هـ) تأكد إن الجدولة شغالة
1. تاب **Actions** في الريبو.
2. لو شفت شريط أصفر فيه زرار **I understand my workflows, go ahead and enable them** → اضغطه.

---

### ملاحظة الريبو الخاص

Apps Script بيقرأ الملفات من `raw.githubusercontent.com` — وده بيشتغل بس مع الريبو **العام**.

**لو الريبو خاص**، عندك حلين:

**الحل الأول (الأسهل):** خلي الريبو **عام**. المحتوى مش سري — هو مقالات هتتنشر على الإنترنت أصلاً.
`Settings` → آخر الصفحة → **Change visibility** → Public.

**الحل الثاني:** استخدم توكن. في Apps Script ضيف Secret Property اسمه `GH_TOKEN`
(من `github.com/settings/tokens` بنوع `classic` وصلاحية `repo` بس)،
ثم غيّر دالة `fetchText_` في `Code.gs` لتحط الهيدر ده:

```javascript
function fetchText_(url) {
  var headers = {};
  var token = PropertiesService.getScriptProperties().getProperty('GH_TOKEN');
  if (token) headers['Authorization'] = 'token ' + token;
  var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true, headers: headers });
  if (res.getResponseCode() !== 200) throw new Error('HTTP ' + res.getResponseCode() + ' for ' + url);
  return res.getContentText();
}
```

---

## 8) Apps Script — المُجدوِل

1. روح على **https://script.google.com** → **New Project** / **مشروع جديد**.
2. امسح الكود الموجود، والصق **كل محتوى** ملف `appsscript/Code.gs` من المشروع.
3. اضغط 💾 **Save**، وسمّ المشروع `AutoBlog Publisher`.

### أ) الإعداد الأول
4. من القائمة فوق اختار الدالة **`setup`** → اضغط **Run** ▶.
5. أول مرة هيسألك صلاحيات:
   - **Review permissions** → اختار حساب جوجل بتاعك
   - **Advanced** → **Go to AutoBlog Publisher (unsafe)** ← دي طبيعية تماماً،
     لأن السكريبت موقع من عندك مش من Google
   - **Allow** / **سماح**
6. افتح **Executions** / **عمليات التنفيذ** → شوف اللوج → هيطبعلك كل الإعدادات الحالية.

### ب) عدّل الإعدادات
7. **Project Settings** ⚙ (أيقونة الترس في الشمال) → **Script Properties** → **Edit**.

| Property | القيمة |
|---|---|
| `RAW_BASE` | `https://raw.githubusercontent.com/USERNAME/autoblog/main` |
| `BLOG_ID` | `1234567890123456789` |
| `SA_JSON` | **محتوى ملف الـ JSON كامل** (من خطوة 4) |
| `POST_HOURS` | `9,13,18` ← مواعيد النشر بتوقيتك |
| `MAX_PER_DAY` | `3` |
| `TZ` | `Africa/Cairo` ← أو `Asia/Riyadh` مثلاً |
| `NOTIFY_EMAIL` | إيميلك (اختياري — للتنبيهات عند الفشل فقط) |

> ⚠️ **`SA_JSON`:** الصق المحتوى كامل في سطر واحد. تأكد إنه يبدأ بـ `{` وينتهي بـ `}`،
> وإن `private_key` موجود جواه (هيكون فيه `\n` كتير — دي طبيعية، متغيرهاش).

8. **Save script properties**.

### ج) تأكد إن التوصيلة شغالة
9. اختار الدالة **`testConnection`** → **Run** ▶.
10. افتح **Executions** → اللوج. **لازم تشوف:**
```
OK  blog name : اسم مدونتك
OK  blog url  : https://...
OK  account   : blogger-publisher@....iam.gserviceaccount.com
OK  queue     : N articles waiting
```

> ❌ لو طلعلك خطأ، شوف [حل المشاكل](#حل-المشاكل).

### د) ثبّت المُشغّل (الخطوة اللي بتخليه تلقائي)
11. اختار الدالة **`installTrigger`** → **Run** ▶.
12. **اختياري:** شغّل **`installDigestTrigger`** → هيبعتلك ملخص أسبوعي كل أحد 8 مساءً.
13. **تأكد:** ⏰ **Triggers** (أيقونة المنبه في الشمال) → لازم تلاقي:
    - `publishScheduled` · **Time-driven** · **Every hour**

**✅ خلاص. من اللحظة دي النظام شغال لوحده.**

---

## 9) اختبار من الطرف للطرف

### أ) ولّد مقالات (لو الـ Queue فاضي)
في تاب **Actions** على GitHub:
1. **generate-daily** → **Run workflow** → `count = 6` → **Run**.
2. استنى دقيقتين → شوف إن الـ run خلص بعلامة ✅ خضرا.
3. اتأكد إن `content/queue/` فيه ملفات جديدة.

> 💡 **الـ 6 مقالات البذرة** (`content/seed/`) هتتنشر الأول تلقائياً —
> دي مكتوبة بإيد ومش محتاجة أي مفتاح AI.

### ب) انشر مقال واحد يدوياً للتجربة
في Apps Script:
1. اختار **`publishNow`** → **Run** ▶.
2. افتح مدونتك → **لازم تلاقي المقال منشور** 🎉
3. شغّل **`status`** → هيقولك الطابور فاضل منه كام وكام يوم محتوى متبقي.

### ج) سيبه يشتغل
من بكرة، في الساعة 9 و1 و6 (أو اللي حددتهم)، هتلاقي مقال جديد منشور لوحده.

---

## التشغيل اليومي: صفر

| الحاجة | كل قد إيه |
|---|---|
| تدخل منك | **ولا حاجة** |
| إيميل يوصلك | **بس لو حصل خطأ** (أو الملخص الأسبوعي لو فعّلته) |
| الطابور يخلص | بعد ~**سنتين** (2561 موضوع ÷ 3 يومياً) |
| تجديد الطابور | تلقائي بالكامل عند نزول المقالات الجاهزة لأقل من 6؛ يستخدم SerpApi فقط، بحد أقصى 6 مقالات في التشغيل وفاصل 12 ساعة بين طلبات التجديد |
| لو عايز منتجات معينة | عدّل `data/products.csv` واعمل Commit |

---

## حل المشاكل

### ❌ `testConnection` بيرجع 403 أو "Not Authorized"
**السبب الأغلب:** الـ Service Account مش مضاف كمؤلف في البلوج.
- بلوجر → **Settings** → **Permissions** → تأكد إن الإيميل موجود تحت **Blog authors**.
- لو مش موجود، اعمل **Invite more users** تاني.

### ❌ 403 مع رسالة `accessNotConfigured`
**السبب:** Blogger API مش مفعّل في المشروع.
- Google Cloud → **APIs & Services** → **Library** → **Blogger API** → **Enable**.
- **تأكد إنك في نفس المشروع** اللي عملت فيه الـ Service Account (الشريط اللي فوق).

### ❌ `invalid_grant` أو خطأ في التوكن
- تأكد إن `SA_JSON` منسوخ **كامل** من `{` لـ `}`.
- تأكد إن `\n` في `private_key` **لسه موجودة** (لو محرر النصوص حوّلها لأسطر حقيقية، التوقيع يبوظ).
- تأكد إن **ساعة جهازك/حسابك** مظبوطة — التوكن حساس للوقت.

### ❌ Apps Script بيقول `queue is empty`
- اتأكد إن `RAW_BASE` صحيح **بالظبط**، وفيه `/main` في الآخر.
- افتح الرابط ده في المتصفح:
  `https://raw.githubusercontent.com/USERNAME/autoblog/main/content/queue/index.json`
  - **لو فتح** → المشكلة في `RAW_BASE` في Script Properties.
  - **لو قال 404** → الريبو خاص، أو مفيش ملفات في الطابور، أو اسم الفرع `master` مش `main`.

### ❌ مفيش مقالات بتتولد على GitHub
- تاب **Actions** → افتح آخر run → شوف اللوج.
- **لو الـ workflow معطّل:** شريط فوق فيه **Enable workflow** → اضغطه.
- **لو الخطأ في المفاتيح:** النظام **هيكمل بالمحرك الأوفلاين** — شوف اللوج، هتلاقي
  `using offline template engine`. ده طبيعي ومش خطأ.

### ❌ الجدولة وقفت بعد شهرين
ده سلوك GitHub المعروف (بيعطّل الـ workflows لو مفيش commits لمدة 60 يوم).
- الـ workflow اسمه **keepalive** بيعمل commit كل 5 أيام بالظبط عشان يمنع ده.
- لو حصل برضه: Actions → **keepalive** → **Enable workflow**.

### ❌ النظام وقف نفسه وبعتلك إيميل
ده الـ **Circuit Breaker** شغال صح — بيحمي حسابك من الحظر بعد 3 أخطاء متتالية.
1. اقرا الإيميل، فيه **آخر خطأ** بالتفصيل.
2. صلّح السبب (غالباً واحد من اللي فوق).
3. في Apps Script شغّل الدالة **`resetBreaker`**.

### ❌ عايز أغيّر عدد المقالات اليومية
**مكانين لازم يتغيروا مع بعض:**
1. GitHub → `config.json` → `schedule.daily_posts`
2. GitHub → Settings → Variables → `DAILY_POSTS`
3. Apps Script → Script Properties → `MAX_PER_DAY`
4. Apps Script → `POST_HOURS` (ضيف ساعات أكتر لو عايز أكتر من 3)

> ⚠️ **متعدّيش 5 يومياً.** الكود فيه حد أقصى مقفول عند 6 لحماية حسابك من فلاتر بلوجر.

---

## ⚖️ التزامات لازم تعرفها

1. **إفصاح الأفلييت** موجود تلقائياً في كل مقال — **متشيلوش**. ده شرط من أمازون،
   ونسيانه ممكن يقفل حساب الأفلييت بتاعك.
2. **حساب Amazon Associates** بيتقفل لو مفيش **3 مبيعات خلال 180 يوم** من التسجيل.
   سجّل لما تكون جاهز تروّج فعلاً.
3. **محتوى AI 100% من غير مراجعة** ممكن يأذي ترتيبك في جوجل. النظام بيكتب بجودة عالية
   وبيفحص كل مقال قبل النشر، بس لو قدرت تراجع مقال من كل 10، النتيجة هتبقى أحسن بكتير.
4. **بلوجر و AdSense** ليهم سياسات ضد المحتوى المكرر/الآلي الكثيف. عشان كده
   الحد الأقصى 3–5 يومياً مش 25.

---

## ✅ تشيك ليست النهاية

- [ ] البلوج موجود وHTTPS مفعّل
- [ ] Blog ID منسوخ
- [ ] Blogger API v3 مفعّل في Google Cloud
- [ ] Service Account متعمل والمفتاح JSON متنزّل
- [ ] إيميل الـ Service Account مضاف كـ **Author** في البلوج
- [ ] مفتاح Groq على الأقل موجود في GitHub Secrets
- [ ] `AFFILIATE_TAG` مضاف في GitHub Secrets
- [ ] `config.json` متعدّل (Blog ID + التاج + repo_raw_base)
- [ ] Apps Script: الكود ملصوق + Script Properties متظبطة
- [ ] `testConnection` رجّع **OK** في كل السطور
- [ ] `installTrigger` اتنفّذ وفيه Trigger كل ساعة
- [ ] `publishNow` نشر مقال فعلي في المدونة 🎉

**مبروك — النظام شغال لوحده من هنا.**
