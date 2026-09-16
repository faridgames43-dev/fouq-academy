# نتائج الاختبار (QA Test Results)

تم تنفيذ جميع سيناريوهات الاختبار الإلزامية 18/18 المطلوبة في المواصفة (البند 73) فعليًا عبر
`tests/qa_tests.py`، والتي تعمل على **نسخة معزولة من قاعدة البيانات الفعلية المُعبّأة
بالبيانات التجريبية** (لا بيانات مزيّفة أو Mock)، عبر استدعاء طبقة منطق الأعمال الحقيقية نفسها
التي تستخدمها شاشات الويب.

بالإضافة لذلك تم التحقق يدويًا عبر HTTP فعلي (curl + جلسة تسجيل دخول حقيقية) من:
- الفحص المزدوج للباركود (Double Scan) لا يخصم مرتين — عبر `/attendance/session/<id>/scan`
- إلغاء التحضير يعكس نفس الحصة تمامًا — عبر `/attendance/<id>/cancel`
- توليد صورة الباركود الحقيقية (Code39) — عبر `/players/<id>/barcode.png`
- توليد تقرير PDF عربي RTL حقيقي بالخط والاتجاه الصحيحين عبر Chromium — عبر `/reports/player/<id>/pdf`
- صلاحيات RBAC: المدرب يُرفض بـ 403 عند محاولة الوصول لـ `/settings`, `/renewals`,
  `/players/<id>/compensation/add`
- تسجيل دخول ولي أمر له ابنان ورؤية الابنين فقط في `/parent`
- تسجيل دخول لاعب ورؤية حسابه الشخصي فقط في `/me`

## نتائج التشغيل الكامل (آخر تشغيل)

```
[PASS] TEST 1: 12 sessions -> attend -> 11 remaining
[PASS] TEST 2: double scan does not deduct twice
[PASS] TEST 3: cancel attendance restores session
[PASS] TEST 4: expired + no sessions -> NO_SESSIONS
[PASS] TEST 5: expired + 3 compensation -> COMPENSATION_ONLY (can attend)
[PASS] TEST 6: attends compensation session -> 2 remaining
[PASS] TEST 7: compensation reaches 0 -> NO_SESSIONS (needs renewal)
[PASS] TEST 8: expired compensation blocks attendance; admin override lifts it
[PASS] TEST 9: renewal preserves compensation + adds regular (12+2)
[PASS] TEST 10: FEFO consumes nearest-expiring compensation batch first
[PASS] TEST 11: coach role is denied 'manage_compensation' permission
[PASS] TEST 12: legacy player completing sessions -> CONVERSION_PENDING (not auto-FOUQ)
[PASS] TEST 13: parent with two sons sees exactly two linked players
[PASS] TEST 14: coach bulk-marks a group -> each player's own record updates
[PASS] TEST 15: redeeming a reward decrements both points and stock
[PASS] TEST 16: player report data path builds without error
[PASS] TEST 17: outstanding-sessions report matches the ledger exactly
[PASS] TEST 18: renewal creates a new row and keeps subscription history

18/18 tests passed.
```

## كيفية إعادة التشغيل

```bash
cd fouq-academy
python3 tests/qa_tests.py
```

السكربت ينسخ `data/academy.db` إلى ملف مؤقت، يشغّل كل الاختبارات عليه، ثم يحذف النسخة
المؤقتة — بيانات العرض التوضيحي التي يراها المستخدم لا تتأثر أبدًا بتشغيل الاختبارات.
