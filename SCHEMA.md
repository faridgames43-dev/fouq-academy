# Database Schema — أهم الكيانات

المخطط الكامل بصيغة SQL في `schema.sql` (SQLite، قابل للترحيل مباشرة إلى PostgreSQL: كل
الاستعلامات SQL قياسية ولا تستخدم أي دوال خاصة بـ SQLite عدا `datetime('now')` التي لها
مكافئ مباشر `NOW()` في Postgres).

## الكيانات الأساسية (P0)
- **branches** — الفروع (جاهز لعدة فروع: كل جدول رئيسي يحمل `branch_id`)
- **users** — حساب موحّد لكل الأدوار (email/phone + password_hash + role)
- **permissions_overrides** — تخصيص صلاحية فردية لمستخدم دون تعديل الكود
- **categories** — الفئات العمرية (البراعم، الأشبال، ...)
- **facilities / groups_ / coaches** — الملاعب والمجموعات والمدربون
- **parents / players / parent_players** — اللاعبون وأولياء الأمور وربطهم (many-to-many)
- **packages** — الباقات (سعر، مدة، عدد حصص، سياسة تجميد/تعويض)
- **subscriptions** — دورة الاشتراك الزمنية (تاريخ بداية/نهاية، حالة، دفع) — **لا تُعدَّل عند
  التجديد، بل يُنشأ سجل جديد دائمًا** لحفظ التاريخ الكامل
- **session_entitlements** — "دفعات" الحصص (REGULAR / COMPENSATION / BONUS / LEGACY)، كل
  دفعة لها كمية متبقية وتاريخ صلاحية مستقل — هذا الجدول هو قلب القاعدة التجارية للفصل بين
  "مدة الاشتراك" و "حق الحضور"
- **session_ledger** — سجل حركة كل حصة (منح/خصم/عكس/انتهاء/تعديل إداري)، Append-only بالكامل
- **training_sessions / attendance** — الحصص والتحضير (قيد Unique على player+session يمنع
  الخصم المضاعف فعليًا على مستوى القاعدة، بالإضافة إلى منطق التطبيق)

## التطوير والتحفيز (P1)
- **assessment_categories / assessment_metrics / assessments / assessment_scores** — التقييم
  الفني (مهاري/لياقي/سلوكي/انضباط) بأوزان قابلة للتعديل
- **levels / player_levels** — مستويات فوق وتاريخ ترقيات كل لاعب
- **points_wallets / points_transactions** — رصيد فوق (Wallet مشتق من Transactions فقط، لا
  يُعدَّل مباشرة أبدًا)
- **achievements / player_achievements** — الإنجازات (قواعد صريحة، ليست عشوائية)
- **rewards / reward_redemptions** — متجر فوق وطلبات الاستبدال (workflow كامل + عكس صحيح)

## النمو والعملاء (P2)
- **referrals** — الإحالات (Pending → Qualified → Rewarded)
- **leads / trials** — CRM والتجربة المجانية → تحويل لاعب
- **renewal_notes** — سجل متابعة التجديد (Renewal CRM)

## عابر لكل الجداول
- **notifications** — مركز التنبيهات (بنية جاهزة لقنوات خارجية لاحقًا)
- **audit_logs** — قبل/بعد/سبب/مستخدم/توقيت لكل عملية حساسة
- **settings** — كل السياسات (مدة صلاحية التعويض، أولوية الاستهلاك، أوزان التقييم، ...) —
  **لا شيء Hardcoded**

## الفهارس
فهارس على كل الأعمدة المستخدَمة في تصفية لوحة التحكم اليومية (`branch_id`, `group_id`,
`player_id`, `session_date`) لضمان استجابة فورية حتى مع نمو البيانات.
