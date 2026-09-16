# قائمة الصفحات (Routes)

## عامة
- `/login` تسجيل الدخول (يعرض حسابات تجريبية حقيقية من قاعدة البيانات الحالية)
- `/notifications` مركز التنبيهات

## الإدارة (Super Admin / PM / Branch Manager / Supervisor)
- `/dashboard` لوحة التحكم الرئيسية + "يحتاج تدخل اليوم"
- `/players` قائمة اللاعبين مع فلاتر | `/players/new` تسجيل لاعب | `/players/<id>` ملف اللاعب الكامل
- `/players/<id>/subscribe` تجديد/اشتراك جديد | `/players/<id>/compensation/add` إضافة حصص تعويضية
- `/players/<id>/points/add` إضافة رصيد فوق | `/players/<id>/assess` تقييم كامل | `/players/<id>/promote` اعتماد ترقية
- `/players/<id>/override` صلاحية حضور إدارية | `/players/<id>/barcode.png` باركود اللاعب (صورة)
- `/attendance` تحضير اليوم | `/attendance/session/<id>` شاشة التحضير (مسح + بطاقات) | `/attendance/new` إنشاء حصة
- `/renewals` مركز التجديدات | `/retention` مركز الاحتفاظ
- `/levels` مستويات فوق | `/rewards` إدارة متجر فوق والاستبدالات
- `/leads` العملاء المحتملون (CRM) والتجارب
- `/reports` مركز التقارير | `/reports/<type>` عرض/CSV/PDF | `/reports/player/<id>/pdf` تقرير لاعب PDF
- `/settings` كل السياسات والباقات والفروع والمجموعات والمدربين
- `/audit-log` سجل التدقيق
- `/search?q=` البحث الشامل

## المدرب
- `/coach` لوحتي | `/attendance` و `/attendance/session/<id>` (بنفس الشاشة، محدودة بمجموعاته)
- `/assessments/quick` الرصد السريع الجماعي | `/players/<id>/assess`
- `/players/<id>/points/add` (بحد يومي)

## ولي الأمر
- `/parent` لوحة الأبناء (تبديل بين أكثر من ابن) | `/reports/player/<id>/pdf`
- `/rewards/store?player_id=<id>` طلب استبدال جائزة لأحد الأبناء

## اللاعب
- `/me` حسابي (Career Mode Hub) | `/rewards/store` متجر فوق (استبدال رصيده الشخصي)
