# Secure Boot на Framework 13 с Arch, ZFS и ZFSBootMenu

Состояние на **7 октября 2026 года**. Это описание конкретного установленного
сетапа и пройденного пути, включая ошибки. Это не универсальная инструкция для
любой прошивки и не дизайн будущего zbm-rs. Приватные ключи в этот документ и Git
не включены; ниже указаны только их назначение и расположение.

## Что в итоге работает

Физическая загрузка Framework с включённым Secure Boot прошла. Ядро
`7.2.8-5-okhsunrog` при запуске сообщает `Secure boot enabled`. Отчёт этой загрузки
содержит `PASS`, `mode=enforce`; его ID совпадает с `/proc/cmdline`.
Lockdown находится в `integrity`, сертификат IMA загружен, подписанные ZFS и
DKMS AmneziaWG работают, пул исправен. Пересборка ядра для последнего исправления
не потребовалась: менялись скрипты и initramfs загрузчика.

После загрузки проявился дефект перечисления EFI-переменных: `efivarfs` показывал
только 15 переменных без стандартных `SecureBoot`, `SetupMode`, `PK`, `KEK`, `db`,
`dbx`, `Boot*` и `Loader*`. Тогда `sbctl status` сообщал
`system is not booted with UEFI`, а `bootctl status` —
`Secure Boot: disabled (unsupported)`, `Measured UKI: no`, `Measured OS: no`.
Прямое чтение firmware по известному имени подтвердило `SecureBoot=1`,
`SetupMode=0`, точное совпадение PK/KEK/db с enrolled наборами и неизменную DBX.
Данные не потеряны. Симптом соответствует разобранному дефекту перечисления
Insyde `VariableRuntimeDxe` в BIOS 3.07; подробности и границы доказательства ниже.
Затем наш подписанный модуль на Rust восстановил 131 файл efivarfs без изменения
firmware variables. Теперь sbctl/efibootmgr/bootctl видят реальное состояние.
Настроены DKMS, штатная автозагрузка модуля и его включение в initramfs ОС перед
udev и ранними TPM-службами. Отдельный recovery-сервис больше не используется.
На физической загрузке в 03:59:28 автоматическое раннее восстановление прошло:
15 → 131 переменная, без ошибок; early TPM setup действительно выполнилась в
initramfs, а обычная setup завершилась с кодом 0. Сам дефект BIOS остаётся.
Затем установлен новый EFI и initramfs с подписанной PCR-политикой для NvPCR:
VM-проверки пройдены. На физической загрузке в 04:52:02 обнаружился предел памяти
TPM и неудачный штатный порядок выделения NvPCR. Загрузка и SRK работают;
После исправления приоритетов физическая загрузка в 05:10:25 подтвердила работу
трёх NvPCR, успешные hardware/login измерения и отсутствие упавших systemd-служб.
`verity` штатно пропущен из-за ограниченной RAM TPM; подробности ниже.

## Машина, система и артефакты

| Компонент | Установленный вариант |
| --- | --- |
| Ноутбук | Framework Laptop 13, Intel Core Ultra Series 1, Core Ultra 5 125H |
| BIOS | INSYDE, версия `03.07` |
| Основная ОС | Arch Linux |
| Основной root | `novafs/arch0/root` |
| Шифрование | Native ZFS encryption, encryption root `novafs`, AES-256-GCM, passphrase |
| Ядро | `7.2.8-5-okhsunrog`, профиль с обязательными подписями и lockdown |
| Загрузчик | ZFSBootMenu `3.1.0`, локальная сборка dracut из нашего ядра |
| ESP | UUID `05E8-D496`, обычная точка монтирования `/mnt/efi` |
| Основной EFI | `/EFI/ZBM/zfsbootmenu.EFI` |
| Резервный EFI | `/EFI/ZBM/zfsbootmenu-backup.EFI` |
| Kernel/initramfs ОС | `/boot/vmlinuz-linux-okhsunrog`, `/boot/initramfs-linux-okhsunrog.img` |
| Репозиторий ядра/патчей | `/home/okhsunrog/tmp_zfs/build/linux-okhsunrog` |
| Staging загрузчика | `/var/lib/zbm-secureboot/staging` |

LTS не является обязательным запасным загрузочным путём. Откат сохраняется через
подписанные доверенные ядра и снимки. Старые неподписанные снимки не должны
автоматически становиться доверенными. Arctern продолжает создавать и удалять
снимки по своему расписанию: специальный удерживаемый `trusted`-снимок и защита
его от удаления не вводились. Поэтому наличие конкретного снимка не гарантируется
загрузчиком; пригодность оставшихся boot-файлов определяется проверками.

Исторический SHA-256 установленного после исправления основного и резервного EFI:

```text
8e61ec362f022ea83a26e09a82c927ecd8b8bab46c53d25437925c17e5ff6594
```

Это идентификатор проверенного образа этого дня, не постоянный хеш для будущих
сборок. Метки времени и подписи могут менять байты при новой генерации.

## Цепочка доверия

```text
UEFI: наши PK / KEK / db, сохранённые сертификаты Microsoft и Framework, DBX
  │ проверка PE/Authenticode-подписи полного EFI-образа
  ▼
Подписанный ZFSBootMenu EFI: stub + ядро загрузчика + initramfs + наши проверки
  │ ранняя загрузка сертификата IMA и подписанной политики IMA
  │ обязательные подписи модулей, forced integrity lockdown
  ▼
Импорт ZFS, ввод пароля, обнаружение окружений и выбор ядра/снимка
  │ подготовка частных копий boot-файлов
  │ проверка подписи манифеста, SHA-256 ядра/initramfs, параметров и root-prefix
  ▼
kexec_file_load
  │ ядро ZBM проверяет PE-подпись выбранного ядра
  │ IMA проверяет подпись выбранного initramfs
  ▼
ZBM verification: PASS → kexec → выбранное ядро и initramfs → Arch
```

Подпись EFI защищает **UEFI → ZBM**, но сама не распространяется на файлы,
которые ZBM позже читает с ZFS. Поэтому нужны отдельные проверки следующего шага.
ZFS encryption защищает конфиденциальность датасетов, но не заменяет проверку
подлинности загрузочного кода: подменённый initramfs способен запросить и украсть
пароль до монтирования root.

Этот сетап проверяет boot-входы выбранного пути. Он не подписывает всё содержимое
root, не доказывает безопасность подписанного кода и не обеспечивает строгий
antirollback. Сохранённые сторонние сертификаты в db разрешают также соответствующие
сторонние EFI-образы; Secure Boot не означает доверие исключительно одному ZBM.

## Ключи: кто чем подписывает

| Роль | Приватный ключ на хосте | Публичная сторона / использование |
| --- | --- | --- |
| Platform Key | `/var/lib/sbctl/keys/PK/PK.key` | `PK.pem`, владелец политики Secure Boot в UEFI |
| Key Exchange Key | `/var/lib/sbctl/keys/KEK/KEK.key` | `KEK.pem`, авторизация обновления баз ключей |
| EFI и boot-манифест | `/var/lib/sbctl/keys/db/db.key` | `db.pem` в UEFI и kernel trust bundle; этим же ключом подписан манифест |
| DKMS | `/var/lib/dkms/mok.key` | `/var/lib/dkms/mok.pub`, сертификат `DKMS module signing key` встроен в ядро |
| IMA | `/var/lib/zbm-secureboot/ima/ima.key` | `ima.pem`, DER `/etc/zfsbootmenu/ima.der`, подписи initramfs и политики IMA |
| PCR policy / NvPCR | `/var/lib/zbm-secureboot/pcr/pcr.key` | RSA-2048 public key `/etc/systemd/tpm2-pcr-public-key.pem`; `.pcrpkey`, `.pcrsig` в EFI, разрешение ранней инициализации NvPCR |
| Штатные модули и ZFS из CI | Временный ключ сборки ядра | Соответствующий публичный сертификат встроен в то же ядро |

В [secureboot-trusted.pem](../secureboot-trusted.pem) находятся три постоянных
**публичных** сертификата: EFI/db, DKMS и IMA. CI проверяет не только `.config`, но
и их наличие в окончательном `certs/x509_certificate_list` ядра.

GitHub Actions продолжает собирать ядро, штатные модули и ZFS. Постоянные локальные
приватные ключи Actions не нужны. Временный ключ самой сборки нужен для модулей
этой сборки; он также используется внутри job для тестовых подписей IMA. Его
закрытая часть остаётся в файловой системе job до завершения одноразового runner.
В просмотренном workflow отдельного шага удаления сразу после подписи нет — не
следует описывать это как немедленный `rm` после сборки. Artifact upload ограничен
пакетами и тестовой диагностикой, ccache — отдельным каталогом кэша. Из установленного
headers-пакета проверен каталог `build/certs`: там только `Kconfig`, не приватный ключ.
Изменения состава artifacts/cache требуют повторной проверки этого условия.

Случайный ключ сборки меняет связанные артефакты: воспроизводимость исходников и
архива не является гарантией побайтово одинаковых подписанных сборок.

Название файлов DKMS `mok.*` не означает, что используется shim/MOK enrollment.
Наш путь — непосредственные UEFI PK/KEK/db и сертификат DKMS во встроенном kernel
keyring. MOK означает Machine Owner Key, а не mock.

## Две разные «политики»

### Boot-манифест ZBM

Рядом с выбранным ядром лежат:

```text
vmlinuz-linux-okhsunrog.zbm-policy
vmlinuz-linux-okhsunrog.zbm-policy.sig
```

Подписанный манифест содержит ровно пять строк:

```text
ZBM-POLICY-v1
<SHA-256 точных байтов ядра>
<SHA-256 точных байтов initramfs>
root=ZFS=novafs/
<точная строка остальных параметров>
```

Подпись проверяется OpenSSL и `/etc/zbm-verification.pem` внутри подписанного EFI.
SHA-256 сам по себе не удостоверяет владельца: аутентичность даёт подпись
манифеста, а хеши связывают эту подпись с конкретными файлами.

`root=` должен быть первым аргументом, принадлежать `novafs/` и пройти допустимый
набор символов. Остальная строка должна совпасть с подписанной. Так сохраняется
выбор датасета/снимка/клона без разрешения произвольно добавлять `init=`, менять
режимы защиты или другие параметры. Это широкое правило **внутри всего `novafs/`**,
а не криптографическое подтверждение происхождения и содержимого любого датасета.

Обёртка добавляет один конечный `zbm.audit_id=<UUID>`. Пользовательский или
повторный audit ID отвергается; это диагностическая метка, не подпись и не правило
авторизации. Верификатор удаляет только допустимый конечный ID перед сравнением.

Реализация: [verify-boot](../boot/verify-boot), [kexec-verify](../boot/kexec-verify).
Обёртка в `enforce` копирует boot-входы в частный `/run/zbm-load.*`, сохраняет
`security.ima` initramfs через `cp --preserve=xattr`, проверяет именно эти копии и
использует только file-based kexec. Legacy loading и неподдерживаемые аргументы
отвергаются. Сообщение `PASS` в enforce выводится после успешной загрузки ядра
через системный вызов; исполнение ранее загруженного ядра — отдельный шаг.

### Политика IMA ядра загрузчика

Файл `/etc/zfsbootmenu/ima-policy` содержит узкое правило:

```text
appraise func=KEXEC_INITRAMFS_CHECK appraise_type=imasig
```

IMA (Integrity Measurement Architecture) умеет **measurement** — хеширование и
учёт измерений, в том числе для TPM, и **appraisal** — отказ в использовании файла
при неверной/отсутствующей подписи. Здесь требуется appraisal initramfs при чтении
для `kexec_file_load`. Это не общий контроль каждого файла и приложения Arch.

Сертификат принимается в ограниченный `.ima` keyring. При включённом Secure Boot
архитектурная политика ядра требует также подписать сам файл пользовательской
политики (`POLICY_CHECK`). Поэтому у него имеется отдельная IMA-подпись
`/etc/zfsbootmenu/ima-policy.sig`.

CPIO initramfs не сохраняет `security.ima` вложенных файлов. Подпись политики
переносится отдельным файлом, после чего ранний hook восстанавливает xattr:

```sh
signature=$(od -An -v -tx1 /etc/zbm-ima-policy.sig | tr -d ' \n')
setfattr -n security.ima -v "0x$signature" /etc/zbm-ima-policy
printf '%s\n' /etc/zbm-ima-policy > /sys/kernel/security/integrity/ima/policy
```

Реальный скрипт также монтирует securityfs, проверяет путь, импортирует сертификат,
отказывает при отсутствующей подписи и проверяет readback правила. Для xattrs
используется настоящий `/usr/bin/setfattr` из `attr`, не BusyBox-подмена.

Это отличается от подписи **самого выбранного initramfs-файла**: его
`security.ima` хранится в ZFS, сохраняется снимками и копируется при подготовке
kexec. Наличие внутреннего файла `x509_ima.der` не означает, что внешний initramfs
уже подписан.

После kexec запускается другое ядро. Политика ядра ZBM не переносится автоматически
в ядро Arch. В текущей ОС видны архитектурные правила `POLICY_CHECK`, измерение
kernel/module reads и принудительные проверки подписей модулей; широкая appraisal
политика всего root не устанавливалась.

## Конфигурация и обновление

Наш профиль `7.2.8-5` включает forced module/kexec signatures и ранний integrity
lockdown. IMA/appraisal/readback/X.509/architecture policy, SHA-256 и `ima` в LSM
проверяются после разрешения Kconfig-зависимостей. В частности:

```text
CONFIG_KEXEC_SIG_FORCE=y
CONFIG_MODULE_SIG_FORCE=y
CONFIG_SECURITY_LOCKDOWN_LSM_EARLY=y
CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY=y
CONFIG_IMA=y
CONFIG_IMA_APPRAISE=y
CONFIG_IMA_READ_POLICY=y
CONFIG_IMA_LOAD_X509=y
CONFIG_IMA_ARCH_POLICY=y
CONFIG_IMA_KEYRINGS_PERMIT_SIGNED_BY_BUILTIN_OR_SECONDARY=y
```

`CONFIG_IMA_WRITE_POLICY` и `CONFIG_IMA_APPRAISE_BOOTPARAM` выключены в текущем
профиле. Выключение Secure Boot в BIOS не отменяет forced проверки этого ядра.
Переключение firmware не является автоматическим разрешением unsigned boot.

ZBM собирается из `/boot/vmlinuz-linux-okhsunrog`, с командной строкой загрузчика
`ro quiet loglevel=4`. Прямое обновление ядра ОС не меняет уже записанный EFI ZBM.
В `/etc/zfsbootmenu/dracut.conf.d/verification.conf`:

```sh
add_dracutmodules+=" zbmverify "
zbm_ima="yes"
zbm_enforce="yes"
```

Обычный способ обновить весь установленный комплект:

```sh
sudo /usr/local/sbin/zbm-update
```

Он берёт lock и последовательно:

1. Подписывает initramfs ОС через IMA, ядро через sbctl и обновляет boot-манифест.
2. Подписывает файл политики IMA локальным IMA-ключом.
3. Вызывает `generate-zbm -c /etc/zfsbootmenu/staging.yaml` вне ESP.
4. Добавляет `.pcrpkey` и `.pcrsig`, подписывает и проверяет staging EFI. Подпись
   PCR-политики разрешает новый и предыдущий доверенный EFI. Затем перестраивает
   initramfs ОС с этой политикой и обновляет его IMA-подпись и boot-манифест.
5. Проверяет UUID ESP, сохраняет предыдущий EFI, пишет `.new`, проверяет подпись,
   делает `sync`, затем переименовывает файл на основной путь.

При последнем исправлении основной **и резервный** EFI заменены исправленным
образом, потому что старый резервный тоже имел ошибку Secure Boot/IMA. Предыдущие
байты отдельно сохранены в `/var/lib/zbm-secureboot/backups/`. Последующие обычные
обновления опять сохраняют предыдущий основной EFI как backup.

Pacman hook `/etc/pacman.d/hooks/99-zbm-sign-boot.hook` подписывает boot-файлы после
обновлений ядра и ряда initramfs-зависимостей. **Он сам не перестраивает EFI ZBM**.
Для обновления встроенного ядра/ZFS/проверок загрузчика нужен `zbm-update`.
Прямой вызов `generate-zbm` — не эквивалент всей процедуре: при изменении политики
нужно обновить её подпись, а выкладка должна проверять подписанный результат.

Основные исходники и установленные команды:

| Исходник | Установка / назначение |
| --- | --- |
| [update-zbm](../boot/update-zbm) | `/usr/local/sbin/zbm-update` |
| [sign-boot](../boot/sign-boot) | `/usr/local/sbin/zbm-sign-boot` |
| [sign-initramfs](../boot/sign-initramfs) | `/usr/local/sbin/zbm-sign-initramfs` |
| [sign-ima-policy](../boot/sign-ima-policy) | `/usr/local/sbin/zbm-sign-ima-policy` |
| Локальный wrapper `sbctl sign` + `sbverify` | `/usr/local/sbin/zbm-sign-image` |
| [module-setup.sh](../boot/module-setup.sh) | dracut `91zbmverify` |
| [load-ima-policy](../boot/load-ima-policy) | ранняя проверка/загрузка политики |
| [ima-pre-udev.sh](../boot/ima-pre-udev.sh) | ранний hook, безопасный отказ |
| [harden-shells.py](../boot/harden-shells.py) | патчи только генерируемого initramfs |
| [recovery-guard.sh](../boot/recovery-guard.sh) | ограничения shell/chroot и discovery recovery |
| [deny-preunlock](../boot/deny-preunlock) | диагностический экран без shell |

Приватные ключи не включаются в EFI, initramfs или пакеты. Возможность подписать
новые файлы зависит от сохранности локальных ключей; firmware enrollment хранит
только публичную часть и не заменяет резервное копирование приватных ключей.

## Enrollment на Framework: выполненная последовательность

Первоначально Secure Boot был Disabled, Setup Mode — Disabled; в firmware стояли
заводские PK/KEK/db. Наш подписанный ZBM ещё не был доверен firmware. Простое
включение Secure Boot на этом этапе могло заблокировать его запуск.

Подготовка сохранена в `/var/lib/zbm-secureboot/enrollment-20261007/`:

- `factory-der/`, `factory-esl/`: исходные публичные сертификаты и базы.
- `efivars/`, `efivars-sha256.json`: байты и хеши переменных, включая DBX/defaults.
- `prepared/`: будущие `PK/KEK/db.esl` и `.auth`.
- `prepared-certificates.json`, `PREPARATION.txt`: состав и результаты проверки.

В подготовленном db остались все пять прежних сертификатов; итоговый db содержит
семь, включая наш `Database Key` и Microsoft Option ROM UEFI CA 2023. KEK содержит
все три прежних сертификата и наш `Key Exchange Key`. PK заменён нашим
`Platform Key`. Приватные ключи не экспортировались в эти наборы.

Для Core Ultra есть сообщения о проблемах после **Erase all Secure Boot Settings**,
особенно с DBX. Мы не проверяли эту опасную альтернативу на BIOS 03.07. Вместо неё
на реальном меню выполнено:

1. F2 → Administer Secure Boot → PK Options → Delete PK.
2. Удалён только `frame.work-LaptopADLPK`; KEK/db/DBX не очищались.
3. Enforce Secure Boot оставлен Disabled, настройки сохранены, выполнена загрузка Linux.
4. Проверены `SetupMode=1`, `SecureBoot=0` и побайтовая неизменность KEK/db/DBX.
5. Выполнен enrollment наших ключей вместе с Microsoft и встроенными Framework:

   ```sh
   sudo sbctl enroll-keys --microsoft --firmware-builtin=db,KEK
   ```

Первый вызов остановился **до записи**: efivarfs-файлы KEK и db имели immutable
флаг. Временно сняли `i` только с этих двух конкретных файлов, повторили enrollment
и восстановили `i` через trap. Глобальный `chattr -i .../*`, очищение DBX и force/yolo
не использовались. Имена переменных:

```text
KEK-8be4df61-93ca-11d2-aa0d-00e098032b8c
db-d719b2cb-3d3a-4596-a3bc-dad00e67656f
```

После записи PK/KEK/db **побайтово совпали** с подготовленными ESL; DBX осталась
неизменной. Setup Mode стал Disabled, а Secure Boot оставался Disabled до ручного
включения в BIOS. Затем F2 → Administer Secure Boot → Enforce Secure Boot → Enabled
→ F10 Save and Exit.

Пароль BIOS задаётся владельцем отдельно и не передаётся инструментам. Он нужен,
чтобы ограничить доступ к выключению Secure Boot/изменению ключей через firmware;
факт его установки в этой сессии не проверен.

## Ошибки, которые реально встретились

### Неверные пароли и misleading recovery

В `novafs` задан `keylocation=file:///etc/zfs/zroot.key`, а у каждого root — свой
`org.zfsbootmenu:keysource`. Файл ключа не читался при диагностике. Загрузчик пытается
разблокировать источник файла через вложенный `load_key`; после неудачи внешний
вызов снова предлагает пароль. OpenZFS даёт до трёх интерактивных попыток за вызов,
то есть при четырёх кандидатах возможно **24 запроса**.

`populate_be_list` пропускает кандидат после отказа. Однако в нашем порядке по
имени Arch идёт последним: успешный ввод даже после 23 неверных попыток ещё позволяет
найти Arch. Изолированный тест с реальным ZFS это подтвердил. Объяснение, что Arch
обязательно был уже пропущен, было ошибочным.

После пустого списка upstream выводил `No boot environments with kernels found`,
`Dropping to an emergency shell...`, ждал 10 секунд и завершал программу меню.
Внешний цикл вызывал `emergency_shell`; наш патч уже заменял его диагностикой.
Ввод на таймере ошибки **не является ещё одной попыткой пароля**.

Точный отказ того физического сеанса не доказан фотографиями: они не показывают
успешное принятие последнего пароля или результат монтирования. После выключения
и правильного ввода с первой попытки машина нормально загрузилась. Мы не меняли
шифрование/ключи датасета и не объявляли его повреждённым.

Исправление UX: причины `Key not loaded`, `Mount failed`,
`No matching kernel/initramfs pair` сохраняются по кандидатам; сообщение о shell
убрано; **M — retry boot menu** повторяет поиск и ввод ключа без перезагрузки.
Причины очищаются перед новым проходом, подписи остаются обязательными.

Ранние ошибки библиотек, dracut и инициализации по-прежнему дают только R/P.
Произвольно возвращаться в недоинициализированное меню или root shell нельзя.

### Главное упущение: политики IMA при Secure Boot

Прежние VM-тесты запускали ядро напрямую либо без firmware Secure Boot. Они
подтверждали IMA-проверку initramfs, но **не дополнительное требование подписать
саму политику**, возникающее при Secure Boot.

При первом включении на реальном Framework появился bootloop:

```text
ima: signed policy file (specified as an absolute pathname) required
cat: write error: Permission denied
Cannot initialize mandatory IMA initramfs policy: refusing to start ZBM
Rebooting.
```

Причина: `cat /etc/zbm-ima-policy > .../ima/policy` передавал текст вместо пути к
подписанному файлу. Hook при отказе делал `reboot -f`. Временное восстановление —
выключить только Enforce Secure Boot, сохранив все enrolled ключи, и загрузить Linux.

Исправление — подпись политики IMA, перенос `.sig`, восстановление xattr,
загрузка по абсолютному пути и отказ без автоматической перезагрузки. В отключении
`IMA_ARCH_POLICY` или ослаблении appraisal необходимости не было.

Резервный EFI также пришлось обновить: подпись старого образа могла быть верна,
но его ранний код имел ту же ошибку. «Подписан» не означает «проверен в нужном режиме».

### Технические ошибки harness и инструментов

- BusyBox `setfattr` нельзя считать заменой `attr`: для appraisal нужен настоящий
  xattr writer. При упаковке добавляется именно `/usr/bin/setfattr`.
- У dracut `/lib` может быть symlink на `/usr/lib`. `lsinitrd -f` не обязан разрешать
  такой путь: сначала проверяется список архива, затем извлекается реальное имя.
- `objcopy --update-section` для PE оставлял старый `VirtualSize`. В test UKI секции
  удаляются и добавляются заново; `.linux` размещается после полного нового initrd.
- `virt-fw-vars --enroll-cert ... --no-microsoft` не создал db с нашим test-сертификатом.
  Его пришлось явно добавить через `--add-db`. Иначе firmware отклоняла test EFI,
  и тест вообще не доходил до политики IMA.
- Superfloppy/каталог FAT и неявный BootOrder давали нестабильный вход в OVMF menu.
  Окончательный harness использует настоящий GPT/FAT ESP, virtio-blk с bootindex
  и явный EFI filepath. Это дефект тестовой оснастки, не доказательство ошибки ZBM.
- Малый second-stage использует GNU `od`; EFI-данные читаются потоком, потом
  отбрасываются четыре байта атрибутов. При прежнем чтении результат `6` оказался
  значением атрибутов вместо значения SecureBoot. Полный исправленный тест проверил `1`.
- Успешный kexec/запуск второго ядра и завершение процесса QEMU — разные события.
  Harness останавливает VM после необходимого маркера; ранние timeout/ошибочные
  запуски не засчитываются как успешные проверки.
- `sbctl export-enrolled-keys` одновременно требовал отсутствующий output directory,
  а Landlock хотел открыть его заранее. Для экспорта публичных резервных копий
  использован `--disable-landlock`; enrollment этим флагом не обходили.

## Recovery: что разрешено и где остаётся граница

Автоматический root shell отключён. При отказе ранней инициализации доступны
reboot/poweroff. При отказе discovery дополнительно доступен M для повторного
поиска. Ctrl+R в уже открытом меню — явный recovery; chroot — Ctrl+J.

Явные shell/chroot сейчас разрешаются guard после проверки ожидаемого GUID пула
`novafs`, включённого шифрования и `keystatus=available`. Это **проверка состояния
ZFS**, а не отдельная аутентификация владельца. GUID — метаданные; сами по себе
GUID и loaded-key status не являются криптографическим доказательством происхождения
пула. Надпись `authenticated menu recovery` в текущем UI следует понимать с этим
ограничением.

Root shell обладает более широкими возможностями, чем проверяющая обёртка;
в частности, ядро не понимает наш манифест параметров. Проверки kernel/module/IMA
не исчезают, но нельзя объявлять все возможные root/direct-syscall/rollback-пути
закрытыми только по успешной обычной загрузке. Полная граница с привилегированным
брокером и отдельной owner-authorized recovery проектируется для zbm-rs; это не
реализованная функция текущих shell-патчей.

## Что было проверено

[recovery-init](../tests/recovery-init) и [recovery-qemu.py](../tests/recovery-qemu.py):
ZBM, dracut, source failures без shell; исчерпание паролей, реальная ошибка
монтирования и отсутствующая пара boot-файлов с последующим успешным retry.
Для key retry проверен настоящий цикл меню; конечный render заменён fixture-маркером,
а не объявлен полноценным UI/OS acceptance. Пул и vdev только в RAM гостя.

[ima-secureboot.py](../tests/ima-secureboot.py) и
[ima-secureboot-init](../tests/ima-secureboot-init): отдельные OVMF vars, временный
EFI test-ключ, SecureBoot=1, GPT/FAT ESP, без host disks/firmware vars/private keys
в госте. Подпись test initramfs создаётся локально **вне** гостя. Проверено:

| Сценарий | Результат |
| --- | --- |
| Подписанная политика | Загрузилась; unsigned initramfs отвергнут, legacy kexec отвергнут, signed initramfs принят; второе ядро запущено, SecureBoot=1 |
| Текст через `cat` | Отвергнут: воспроизведение исходной ошибки |
| Изменённый файл политики | Отвергнут, диагностический экран без bootloop |
| Чужая IMA-подпись | Отвергнута, диагностический экран без bootloop |
| Отсутствующая `.sig` | Отказ до handoff, диагностический экран без bootloop |

В signed-policy gate native kexec вызывается непосредственно для независимой
проверки слоя ядра. Это не тест всех правил manifest/command-line wrapper; для
них существуют отдельные IMA/kexec сценарии. ShellCheck, Python syntax и
`git diff --check` прошли.

Физическая проверка **7 октября, 02:40:55 Europe/Moscow**:

```text
kernel: 7.2.8-5-okhsunrog
kernel startup: Secure boot enabled
zbm.audit_id: 57b7595c-016c-4492-b687-7f6624d24bb4
ZBM report: PASS / enforce
lockdown: integrity
ZFS: healthy
DKMS AmneziaWG: loaded, signer DKMS module signing key
failed systemd units: 0
```

Диагностические материалы:

- `/home/okhsunrog/tmp_zfs/build/zbm-flow-review/REPORT.txt`: разбор recovery/discovery.
- `.../IMA-SECUREBOOT-VALIDATION.txt`: результаты policy fix и физической загрузки.
- `.../retry-tests/logs-menu/`: recovery VM-проверки.
- `.../ima-secureboot/run-010/`: окончательный успешный firmware-enabled gate.

Маркеры измерений, обычные журналы и JSON audit — диагностика, не удалённая
криптографическая attestation. Сохранённый отчёт должен совпадать с ID и всей
командной строкой текущей загрузки; старый `PASS` на ESP ничего не подтверждает.
Сам отчёт записывается до native load; консольный PASS в enforce — после него.
Физическое достижение ОС с совпадающим ID подтверждено отдельно.

## EFI-переменные сохранились: сломано их перечисление

Проверка 7 октября установила различие между двумя операциями firmware:
`GetNextVariableName()` перечисляет имена, а `GetVariable()` читает конкретное
имя/GUID. Стандартные переменные отсутствовали в каталоге efivarfs, но прямое
чтение через существующий интерфейс ядра вернуло:

| Переменная | Атрибуты | Результат прямого чтения |
| --- | --- | --- |
| `SecureBoot` | `0x6` | `1`, включён |
| `SetupMode` | `0x6` | `0`, ключи enrolled |
| `PK` | `0x27` | 1255 байт; совпадает с `prepared/PK.esl` |
| `KEK` | `0x27` | 5073 байта; совпадает с `prepared/KEK.esl` |
| `db` | `0x27` | 9635 байт; совпадает с `prepared/db.esl` |
| `dbx` | `0x27` | 20764 байта, 432 SHA-256 записи; совпадает с backup до enrollment |
| `BootCurrent` | `0x6` | `0003` |
| `BootOrder` | `0x7` | `0003, 0000, 2001, 2002, 2003` |
| `StubInfo` | `0x6` | `systemd-stub 262-1-arch` |
| `StubPcrKernelImage` | `0x6` | `11` |
| `LoaderFirmwareInfo` | `0x6` | `INSYDE Corp. 0.775` |
| `LoaderFirmwareType` | `0x6` | `UEFI 2.90` |

SHA-256 текущей DBX payload:

```text
0c23b04fc76278613db777f27990461d3948451a23bf554ebb4a81a652138e32
```

Диагностика обошлась без записи NVRAM: отсутствующее имя открывалось с
`O_RDONLY | O_CREAT | O_EXCL`, создавая только временный inode efivarfs. `read()`
вызывает firmware GetVariable. Никаких `write()`/`unlink()` не было. После
закрытия ядро автоматически удаляет такой inode с нулевым `i_size`; набор имён
до и после каждой проверки совпал, осталось 15 файлов. Приватные ключи и
содержимое vendor password variables не читались.

Это проверено по коду Linux: [create/unlink](https://github.com/torvalds/linux/blob/v7.2/fs/efivarfs/inode.c)
и [read/write/release](https://github.com/torvalds/linux/blob/v7.2/fs/efivarfs/file.c).
Нельзя заменять эту диагностику обычным удалением файлов: `unlink()` efivarfs
вызывает удаление самой firmware variable. Повторное монтирование в другом
namespace также не доказывает повторное перечисление: efivarfs использует
[get_tree_single](https://github.com/torvalds/linux/blob/v7.2/fs/efivarfs/super.c)
и разделяет один superblock между такими mounts.

### Причина в прошивке

[Разбор `VariableRuntimeDxe`](https://github.com/melynx/insyde-uefi-variable-recovery/blob/master/docs/framework-follow-up.md)
обнаружил ошибочный код именно в Framework Laptop 13 Intel Core Ultra Series 1
BIOS 3.07. Автор анализировал Framework статически и проверял восстановление на
Lenovo; это не его аппаратный тест Framework. Наше прямое чтение выше независимо
подтверждает ключевой симптом на нашем ноутбуке.

Механизм по [анализу прошивки](https://github.com/melynx/insyde-uefi-variable-recovery/blob/master/docs/mechanism.md):

1. Linux начинает перечисление с пустого имени.
2. Firmware проверяет первую запись основного variable store.
3. Если у неё нет `EFI_VARIABLE_RUNTIME_ACCESS`, выбирает запасной store,
   вместо того чтобы пропустить запись и продолжить поиск в основном.
4. Linux получает только короткий список defaults/vendor variables.

История удаления и обновления Secure Boot records может менять первую запись
и проявлять дефект. Нашу конкретную первую физическую запись не установили:
SPI/NVRAM не дампили, полный seeded GetNextVariableName scan не выполняли.
Поэтому точный момент изменения порядка store остаётся неизвестным.

Это не специфичный симптом ZBM/kexec: [issue #76](https://github.com/FrameworkComputer/SoftwareFirmwareIssueTracker/issues/76)
содержит такой же отказ с systemd-boot, стандартными ядрами и сообщения о Core
Ultra BIOS 3.07. Неполный каталог не является доказательством удаления ключей
или выключения firmware Secure Boot.

### Почему сохранение DBX не предотвратило дефект

Мы сохранили существующую DBX при enrollment, и прямое чтение это подтвердило.
Однако существующая база уже отличалась от `dbxDefault`: обе содержат по 432
уникальных SHA-256 записи, 430 совпадают, по две различаются. Backup заводской
`dbxDefault` совпадает с нынешней `dbxDefault` побайтно.

История fwupd показывает успешное прежнее обновление DBX `20250507 → 20250902`,
завершённое **21 октября 2025, 15:37:26 UTC**, и обновление BIOS `3.06 → 3.07`
**2 июля 2026, 10:43:34 UTC**. Это объясняет, почему «не трогать DBX сейчас» не
равнозначно «variable store никогда не менялся». Само по себе это не доказывает,
что именно обновление 2025 года стало непосредственным триггером нынешнего отказа.

### Установленное восстановление на Rust

На проверенной [официальной странице](https://resources.frame.work/downloads/laptop-13/intel-core-ultra-series-1/3.07/)
последний stable BIOS — 3.07, уже установленный у нас. Подтверждённого исправленного
релиза для этой платы в найденных источниках нет.

Реализация: [modules/insyde-efivarfs](../modules/insyde-efivarfs/README.md).
Обход, проверки и модель для тестов написаны на Rust; небольшой C-адаптер
вызывает EFI API и создаёт inode VFS. В установленном ядре уже есть CONFIG_RUST=y,
метаданные для внешних модулей и нужный toolchain `nightly-2026-07-22`.
Пересборка ядра не понадобилась. Метод основан на
[upstream](https://github.com/melynx/insyde-uefi-variable-recovery/tree/bd0d2ce8ad31c098799844bd0993afa3f1602d50),
весь код и документация которого прочитаны перед восстановлением.

Наш Framework не заполняет Attributes при некоторых GetVariable-запросах только
размера. Первые диагностические версии модуля отказали до создания файлов.
Попытка с ненулевым указателем и нулевой ёмкостью ничего не изменила. По UEFI
Attributes должны возвращаться и при BUFFER_TOO_SMALL; здесь этот контракт
нарушен. В адаптации неизвестные атрибуты представлены явно, без выдуманной
битовой маски. Положительный ответ runtime GetVariable подтверждает доступность
записи; если атрибуты возвращены, RUNTIME проверяется. У seed проверка строже:
читается только публичный PlatformLang в ограниченный буфер, его реальные атрибуты
должны содержать RUNTIME. Произвольные payload/password variables не читаются.

Физические проверки этой загрузки:

```text
unsigned .ko: Key was rejected by service
signer: DKMS module signing key, certificate in .builtin_trusted_keys
03:18:43: dry-run empty-start=15, seeded=131, no files created
03:19:19: recovery created=116, present=15, skipped=0, failed=0
after unload: 131 entries remain
PK / KEK / db / dbx: raw-byte hashes unchanged
sbctl: Secure Boot Enabled, Setup Mode Disabled
efibootmgr: original BootOrder and Boot0003 ZFSBootMenu visible
bootctl: firmware UEFI 2.90 / INSYDE 0.775, Secure Boot enabled (user)
fresh systemd-analyze: measured-os condition succeeds
03:23:36: DKMS-installed module and enabled oneshot service succeeded
```

Оба обхода завершаются именно EFI_NOT_FOUND, число итераций ограничено, дубликаты
и неверные длины отвергаются. Восемь тестов проверяют также ошибки firmware/памяти,
неподтверждённый seed, отсутствующие атрибуты и исправную прошивку.

Установленные файлы:

- `/usr/src/insyde-efivarfs-0.1.0/`: исходники DKMS.
- `/usr/lib/modules/7.2.8-5-okhsunrog/updates/dkms/insyde_efivarfs.ko.zst`: подписанный модуль.
- `/etc/dkms/insyde-efivarfs.conf`: расположение matching Rust toolchains для DKMS.
- `/etc/modules-load.d/insyde-efivarfs.conf`: штатная автозагрузка модуля.
- `/etc/modprobe.d/insyde-efivarfs.conf`: параметр `expose=1`.
- `/etc/systemd/system/systemd-modules-load.service.d/10-insyde-efivarfs-ordering.conf`:
  порядок перед udev, tpm2.target, ранней/обычной TPM setup, initrd PCR phase и OS separator.
- `/etc/dracut.conf.d/insyde-efivarfs.conf`: добавление модуля и dracut-plugin в initramfs ОС.
- `/usr/lib/dracut/modules.d/12insyde-efivarfs/`: build-time plugin и три runtime-конфига.

Повторные запуски первоначального сервиса в 03:23:36 и 03:25:07 дали `created=0,
present=131, failed=0`: восстановление идемпотентно. Затем сервис и helper удалены;
модуль загружается штатным systemd-modules-load и остаётся загруженным.
DKMS пересобирает и
подписывает модуль при обновлении ядра; отсутствующий matching Rust toolchain
вызывает явную ошибку сборки. Автоматический запуск при следующей физической
загрузке ещё требует проверки. Модуль восстанавливает доступные записи после
seed и известные необходимые переменные; это не доказательство полноты всех
неизвестных записей перед seed.

Сброс Secure Boot к заводскому состоянию с повторным enrollment помогал другим
пользователям, но меняет конфигурацию и список отзывов. Для нашей работающей
цепочки это не первый шаг: сброс может удалить текущую обновлённую DBX и наши
ключи. При диагностике firmware настройки, ключи и DBX не меняли. Установленный
модуль также не вызывает SetVariable. Его работа меняет только представление
efivarfs в памяти; само исправление firmware требует нового BIOS от Framework/Insyde.

### Ранняя загрузка в initramfs ОС

В systemd 262 PID 1 монтирует efivarfs до запуска unit-ов; на этом хосте обычное
монтирование `rw,nosuid,nodev,noexec`. Поэтому отдельный remount/helper не нужен.
Штатный module loader загружает подписанный DKMS-модуль с `expose=1` прежде, чем
udev обнаружит TPM и ранние службы проверят `ConditionSecurity=measured-os`.

Одного `Before=tpm2.target` недостаточно: generator может не подключить target,
когда встроенный драйвер уже создал TPM device. Drop-in также задаёт прямой
`Before` для `systemd-tpm2-setup-early.service`, `systemd-tpm2-setup.service`,
`systemd-pcrphase-initrd.service` и `systemd-pcrosseparator.service`.
Также заданы `Wants=tpm2.target`, чтобы setup дождалась TPM udev database и
NvPCR workaround даже со встроенным драйвером, и порядок перед ранними
PCR/report sockets, которые тоже проверяют measured-OS/UKI conditions.
Проверка графа systemd не обнаружила циклов. Модуль не выполняет EFI writes;
ограничения Secure Boot и принудительная проверка подписей модулей остаются действовать.

Dracut-plugin включает модуль и его конфиги, early TPM service/binary,
systemd-pcrextend, TSS libraries, TPM udev rules, пользователя tss и hwdb.
Прежний initramfs не содержал early setup и локальной hwdb. Свойство
`TPM2_BROKEN_NVPCR` читается systemd из udev database устройства `tpmrm`, поэтому
одного hwdb-файла в корневой системе недостаточно для ранней службы.
В hostonly initramfs теперь включена `/etc/udev/hwdb.bin` с локальным правилом.

При обновлениях порядок hooks: `70-dkms` → `90-dracut` → `99-zbm-sign-boot`.
После ручной пересборки образа обязательны IMA-подпись initramfs и новая подпись
boot-манифеста; проверка файлов перед установкой не заменяет эти подписи.
Образ ОС содержит ключ ZFS, поэтому кандидаты/резервные копии хранятся в каталоге
root-only и не используются как VM fixtures. VM получает отдельный generic
initramfs без host config/key, отдельные OVMF variables, одноразовый EFI-ключ и swtpm.

На этапе раннего восстановления EFI initramfs был установлен атомарно в
`/boot/initramfs-linux-okhsunrog.img` (позднее заменён образом с NvPCR-политикой).
IMA-подпись и новый boot-манифест проверены `zbm-sign-boot`; SHA-256 образа:
`427440b9723b78de8dc56a43e90cecda502c15507a870bdf36be522a2a3e5652`.
Предыдущие kernel/initramfs/manifest сохранены с xattrs в root-only каталоге
`/var/lib/zbm-secureboot/efi-autoload-20261007/backup/`.
Это файловая резервная копия; расписание и удержание снимков arctern не менялись.

Тесты [efi-autoload-qemu.py](../tests/efi-autoload-qemu.py) и
[efi-autoload-init](../tests/efi-autoload-init) проверили реальный stock
systemd-modules-load и early TPM setup под OVMF Secure Boot с swtpm:

- Подписанный DKMS-модуль принят; module loader завершился раньше udev и TPM setup.
- `ConditionSecurity=measured-os` прошла; SRK создан, status 69 принят, NvPCR workaround применён.
- Вторая fixture без подписи модуля отклонена ядром; module loader завершился с `exit-code`.

Логи: `/var/lib/zbm-secureboot/efi-autoload-20261007/vm-007/signed.log` и
`.../vm-008/unsigned.log`. OVMF перечисляет EFI variables нормально: тест не
воспроизводит дефект Insyde. Его физическое восстановление уже проверено вручную;
автоматическое раннее восстановление установленным initramfs ещё требует обычной
перезагрузки ноутбука.

## TPM: SRK проверен, установлена PCR-политика для NvPCR

Изначальная ошибка была `systemd-tpm2-setup-early.service` (Early TPM SRK Setup).
До подготовки PCR-политики для systemd 262 использовался локальный workaround:

```text
/etc/udev/hwdb.d/60-tpm2-local.hwdb

tpm2:*:mfINTC:vsMTL:*
 TPM2_BROKEN_NVPCR=1
```

Комментарий этого hwdb-файла исправлен: проблема не в отсутствии UKI.
Сейчас EFI ZBM использует systemd-stub с ядром/initramfs. Подпись такого образа,
signed boot-манифест и signed политика IMA не являются подписанной PCR-политикой
TPM. Успешная настройка последней/NvPCR в этой сессии не подтверждена.

На первоначальной загрузке failed units не было, но обе TPM setup-службы были
**пропущены** из-за скрытых EFI-переменных:

```text
Early TPM SRK Setup skipped, unmet condition check ConditionSecurity=measured-os
TPM SRK Setup skipped, unmet condition check ConditionSecurity=measured-os
ConditionResult=no
ActiveState=inactive
Result=success
```

`Result=success` здесь не означает, что успешно прошла SRK/NvPCR setup: сервис
не запускался. Теперь прямое чтение подтвердило скрытый `StubPcrKernelImage=11`.
[systemd 262](https://github.com/systemd/systemd/blob/v262/src/shared/efi-loader.c)
при отсутствии явных overrides определяет measured-os через этот маркер; отсутствие
файла в efivarfs даёт отрицательный результат. Debug-проверка `systemd-analyze
condition ConditionSecurity=measured-os` показала ENOENT для этого пути. Маркер
относится к измерению EFI-образа ZBM, а не доказывает измерение всех поздних
kexec-входов в PCR 11.

Простого временного O_CREAT inode недостаточно для проверки condition: его
`i_size=0`, и systemd считает его uncommitted, хотя ручной read возвращает значение.
Восстановление для системных утилит должно выставить верный размер inode, как делает
наш модуль. Контрольная проверка condition с нулевым inode осталась отрицательной.
После полноценного восстановления новая `systemd-analyze condition
ConditionSecurity=measured-os` проходит, bootctl сообщает Measured UKI/OS: yes.
Однако PID 1 уже мог закешировать прежний отрицательный ответ: пропущенные службы
этой загрузки не запускались. Первоначальный oneshot после local-fs был слишком
поздним. Новая штатная автозагрузка в initrd готовит переменные до этих checks;
на физической загрузке в 03:59 это уже подтвердилось. Early setup выполнилась
в initramfs с принятым статусом 69, обычная setup в root завершилась с кодом 0.
Повторная early setup в root штатно пропускается по
`ConditionPathExists=!/run/systemd/tpm2-srk-public-key.pem`: ключ уже подготовлен.

В 03:39:00 выполнен прямой запуск настоящего `systemd-tpm2-setup --early=yes
--graceful` в transient unit с теми же `SuccessExitStatus`, что у stock service:

```text
SRK already stored in the TPM.
SRK saved /run/systemd/tpm2-srk-public-key.pem matches SRK in TPM2.
4 NvPCRs failed to initialize, proceeding anyway.
ExecMainStatus=69 (UNAVAILABLE, accepted by stock service)
Result=success
```

Сообщение о неподдерживаемых NV indexes в этом запуске вызвано нашим явным
`TPM2_BROKEN_NVPCR=1`; это не новое доказательство неисправности TPM. SRK рабочий,
сбрасывать TPM не требуется. Настройка NvPCR намеренно обходится. Начальный запуск
тестового transient unit без stock `SuccessExitStatus` неверно классифицировал
тот же код 69 как failure; повторный запуск с настоящими настройками сервиса прошёл.

Исправление [systemd PR #43948](https://github.com/systemd/systemd/pull/43948),
которое делает отсутствие initial write PCR policy штатным случаем, на момент
проверки ещё не merged. Однако для работы NvPCR ждать этот PR не требуется:
подготовлена настоящая подписанная PCR-политика. Ни signed IMA policy, ни
boot-манифест не заменяют её.

Secure Boot, TPM measured boot, NvPCR setup и автоматическая разблокировка ZFS —
разные механизмы. Текущая загрузка не использует TPM для снятия пароля ZFS.
Локальный hwdb-workaround удалён, hwdb пересобрана. На физической загрузке
в 04:52:02 свойство `TPM2_BROKEN_NVPCR=1` действительно отсутствует, ранняя
инициализация NvPCR запускается. Это не гарантирует наличия свободной памяти TPM.

### Что даёт NvPCR и как подписана их политика

NvPCR — дополнительные PCR-подобные регистры на основе TPM NV indexes типа
`TPM2_NT_EXTEND`. Systemd определяет `hardware`, `login`, `verity` и `cryptsetup`
в `/usr/lib/nvpcr/*.nvpcr`. Они учитывают отдельные события текущей загрузки;
сами по себе не включают запрет доступа. Их значения могут использоваться агентом
аттестации или другой политикой. Требования будущего корпоративного VPN не
исследовались; использование TPM для ключа сертификата не требует этих NvPCR.

Первый extend NvPCR разрешён только при подходящем PCR 11 в ранней фазе.
Подпись связывает ожидаемое измерение с `policyref=initrd` и
`phase=enter-initrd`. После `leave-initrd` это состояние PCR 11 уже недоступно.
Systemd также измеряет публичные атрибуты NV indexes в PCR 9, затем записывает
separator: пересоздание индекса с другой политикой не даёт той же аттестации.

[pcr-policy.py](../boot/pcr-policy.py) извлекает точные VirtualSize bytes PE
ресурсов, добавляет публичный ключ, вызывает `systemd-measure sign` и встраивает
JSON подписи в `.pcrsig`. Приватный ключ читает только systemd-measure; он не
попадает в EFI/initramfs/Git/Actions. `.pcrpkey` измеряется в PCR 11;
`.pcrsig` не измеряется, чтобы не создавать циклическую зависимость. Исходный
layout linked stub, включая `.sbat`, сохраняется; `.linux` остаётся последней.
Весь результат затем подписывается EFI/db-ключом.

В нашей цепочке PCR 11 описывает **EFI-образ ZBM и фазы загрузки ОС**.
Kexec не выполняет второй systemd-stub и не переносит его synthetic `/.extra`.
Поэтому публичный ключ и JSON подписи отдельно включены в подписанный initramfs
ОС через `zbm_pcr_policy=yes`. Initramfs ОС не встроен в EFI ZBM, поэтому такое
включение не меняет подписываемое измерение ZBM. Эта PCR-политика не фиксирует
хеш выбранного ядра/initramfs ОС в PCR 11: их подлинность обеспечивает описанная
выше цепочка EFI signatures, IMA и signed boot-манифест.

[sign-image](../boot/sign-image) установлен как `/usr/local/sbin/zbm-sign-image`.
Он обновляет PCR-политику, EFI-подпись, initramfs ОС, IMA-подпись и boot-манифест;
при ошибке восстанавливает свои предыдущие файлы. Файловые backup-каталоги
root-only сохраняются для восстановления. `zbm-update` вызывает его перед
публикацией EFI на ESP; post-hook `90-sign-zbm` также использует этот helper.
При очередном обновлении включаются подписи для нового и предыдущего доверенного
EFI, что сохраняет один firmware backup. Резервный старый EFI после текущего
обновления действительно остаётся предыдущим доверенным образом.

Первое развёртывание PCR-политики 7 октября:

- EFI SHA-256: `b2f96bdf3f2dc515e5a5e02529be646eaba5f50e64204e59479f1198c8bb2959`.
- Initramfs ОС SHA-256: `c0c29c6d6b84beed9d2afefaa495f236d0e14ca4cc4c25d2be5ecd050d429ce7`.
- Ожидаемый PCR 11 SHA-256 **в фазе enter-initrd**:
  `2ec5c6097c06a0c6058792108bb08445845ad95d2a583904794a2ca8e9de4641`.
- Предыдущий EFI отдельно сохранён; новые policies включают его точное измерение.
- Конфигурация до изменения: `/var/lib/zbm-secureboot/nvpcr-20261007/deploy-backup/`.
- Последняя рабочая пара boot-файлов: `/var/lib/zbm-secureboot/pcr-update.Zb0c0B/`.
- Проверки и hashes: `/var/lib/zbm-secureboot/nvpcr-20261007/validation.json`.

Для старого EFI расчёт PCR 11 после `enter-initrd:leave-initrd:sysinit:ready`
**точно совпал с аппаратным значением текущей загрузки**:
`9f2abdd9cc41bed34f69d139be0a8948c7bdaa43019b561f752ef2ec30655c71`.
Новые/резервные PCR-подписи также независимо проверены через OpenSSL по digest,
рассчитанному `systemd-measure policy-digest`. Проверено совпадение публичного
ключа и JSON в EFI и initramfs ОС, наличие обеих separator-служб и отсутствие
циклов в графе systemd.

VM с OVMF Secure Boot и swtpm проверила пять случаев: правильную политику,
неправильный policyref, неправильную фазу, изменённую криптографическую подпись
и успешный переход через signed kernel + IMA-signed initramfs + kexec. В успешных
случаях все четыре NvPCR инициализировались, early setup вернула код 0.
Логи: `nvpcr-20261007/vm-006/*.log` и `vm-007/nvpcr-tampered.log` под
`/var/lib/zbm-secureboot/`. Для этих случаев используется QEMU TCG: KVM + swtpm
на данном окружении иногда не выполняли firmware measurement и не публиковали
StubPcrKernelImage, что делало прогон непригодным для проверки политики.

### Физическая загрузка: предел RAM и порядок выделения

7 октября в 04:52:02 физический TPM Intel MTL не смог выделить первый по штатному
приоритету NvPCR `verity`: `TPM_RC_NV_SPACE` (`0x14b`). Early setup затем пропустила
все четыре NvPCR, вернула 73 (`CANTCREAT`, принят службой как успешный статус).
Обычная SRK setup завершилась с кодом 0. EFI recovery снова восстановил 116
переменных, Secure Boot включён, ZBM audit содержит `PASS`, `mode=enforce`.

После switch-root `systemd-pcrproduct` попыталась поздно создать `hardware`.
Само сообщение TSS `0x14c` означает только, что индекс уже существует. Повторный
запуск с debug подтвердил совпадение ожидаемого NV Name и write policy и reuse
индекса без его пересоздания. Фатальная ошибка возникает дальше: текущий PCR 11
уже соответствует фазе `ready`, поэтому подписи для `enter-initrd` нет.
`Couldn't find signature ...` возвращает `ENOSTR` (`Device not a stream`).
Расширять подписанную политику на поздние фазы ради обхода этой ошибки нельзя:
это разрушило бы ограничение первой записи ранней фазой.

Расчёт установленного EFI после `enter-initrd:leave-initrd:sysinit:ready` точно
совпал с физическим PCR 11:
`8c6f894add00f2a0cc8cefa3da76df28d9946346ac5205eb3f90ba04b0255c13`.
Политика и публичный ключ согласованы с образом; первичная ошибка — выделение
памяти до проверки подписи, а не другой EFI или ошибочный расчёт PCR.

Прочитаны только **публичные метаданные** всех NV indices. В стандартных адресах
systemd найдены `hardware` с новой policy и non-orderly атрибутами и `cryptsetup`
со старыми owner/authwrite + orderly атрибутами. Контрольное выделение собственных
временных 32-byte NT_EXTEND indices с такими же атрибутами/policy показало:
один дополнительный orderly индекс помещается, второй возвращает `0x14b`;
non-orderly индекс тоже помещается. После проверки удалены только созданные
проверкой индексы, исходный набор handles сохранён. Ни содержимое чужих NV
indices, ни приватные TPM objects не читались; TPM clear не выполнялся.

`orderly=true` использует ограниченную RAM TPM, `orderly=false` — NVRAM.
Перенос `login`/`cryptsetup`/`verity` в NVRAM увеличил бы постоянные записи;
для этого исправления он не нужен. Hardware остаётся штатным non-orderly.
Штатная systemd 262 умеет мигрировать старые NvPCR, но ранний setup после первой
ошибки нехватки памяти прекращает попытки для всех оставшихся приоритетов.
Поэтому неиспользуемый на этой ZFS-системе `verity` не должен идти первым.

Локальные JSON overrides из `modules/insyde-efivarfs/nvpcr/` установлены в
`/etc/nvpcr/` и включаются в initramfs ОС нашим dracut-модулем:

| NvPCR | Приоритет | Хранилище | Handle |
| --- | ---: | --- | --- |
| hardware | 100 | NVRAM, как в штатной конфигурации | `0x01d10200` |
| cryptsetup | 200 | RAM | `0x01d10201` |
| login | 300 | RAM | `0x01d10203` |
| verity | 800 | RAM, последним | `0x01d10202` |

Handles и алгоритмы не меняются. `cryptsetup` сохраняет возможность миграции уже
занятого индекса, `login` получает оставшуюся RAM, `verity` при нехватке места
штатно пропускается. Его значение будет `-`; это частичная поддержка NvPCR,
а не обещание наличия четырёх регистров. Выход early setup 73 в таком случае
ожидаем; `systemd-pcrproduct` должна успешно расширить уже инициализированный
`hardware`. TSS может печатать `0x14c` при reuse существующих indices: оценивать
нужно последующее сообщение и результат службы, а не этот код отдельно.

Для регрессии VM получает старые hardware/cryptsetup indices и дополнительные
RAM indices до настоящего `NV_SPACE` в **собственном swtpm**. Контрольный случай
`nvpcr-limited-default` проверяет пропуск всех четырёх со штатными приоритетами.
`nvpcr-limited-kexec` проверяет новую очередность, миграцию, успешную инициализацию
hardware/cryptsetup/login через signed kernel + IMA-signed initramfs + kexec,
пропуск verity и успешное измерение product ID. Все команды наполнения выбирают
fixture TCTI явно; к физическому TPM они не обращаются.

Оба случая прошли: логи `nvpcr-20261007/vm-008/nvpcr-limited-default.log` и
`nvpcr-limited-kexec.log` под `/var/lib/zbm-secureboot/`. Исправленный initramfs
установлен, его SHA-256:
`5d907c77f76f7fb1eed10f622810fbc6de5bdff49ea3ce32065956460a8cfc9a`.
IMA-подпись проверена; boot-манифест обновлён и полный host verifier прошёл.
Публичный PCR key/signature JSON, байты EFI и байты ядра не менялись.
Ошибка `systemd-pcrproduct` **в boot 04:52:02** оставалась: поздний restart не
восстанавливает пропущенную раннюю инициализацию. `reset-failed` для скрытия
ошибки не выполнялся. Исправление вступило в силу на следующей загрузке.

Диагностические результаты: `nvpcr-20261007/physical-diagnosis/`;
backup boot-файлов и предыдущего dracut-модуля: `nvpcr-20261007/priority-backup/`
под `/var/lib/zbm-secureboot/`. TPM не сбрасывался,
firmware keys/DBX и EFI ZBM не менялись, ядро не пересобиралось.
Автоматическая разблокировка ZFS не включалась.

### Аппаратная приёмка NvPCR после исправления

Загрузка 7 октября, 05:10:25 Europe/Moscow, boot ID
`738a7d34-60ff-4c5b-b9ff-d6d6a28b9dd7`:

- `systemd-tpm2-setup-early` в initramfs сообщила `3 NvPCRs initialized` и штатно
  пропустила один NvPCR, `verity`, из-за `NV_SPACE`. Статус службы успешный.
- `systemd-tpm2-setup` и `systemd-pcrproduct` завершились с кодом 0;
  `hardware` действительно расширен строкой product ID.
- `login` действительно расширен записями пользователей UID 970 и UID 1000;
  обе login-службы завершились с кодом 0.
- `cryptsetup` инициализирован, но его значение
  `f5a5fd42d16a20302798ef6ed309979b43003d2320d9f0e8ea9831a92759fb4b`
  равно `SHA256(zero32 || zero32)`: это только начальная запись. LUKS на root нет,
  ZFS passphrase не измеряется этим механизмом.
- `systemctl --failed` пуст. TSS сообщения `0x14c` при существующих indices и
  `0x14b` при пропуске verity остаются в журнале; это не failed units и не отказ
  проверки подписи. Схема предоставляет три NvPCR, а не четыре.
- Secure Boot включён, lockdown `integrity`; Rust EFI recovery снова восстановил
  15 → 131 переменную (created 116, failed 0).
- ZBM audit `1133d10b-e7ef-4572-88b5-e60462bd7757` совпал с текущей cmdline,
  результат `PASS`, режим `enforce`. Hash загруженного initramfs совпал с новым
  установленным образом. ZFS pools healthy.

Таким образом, подписанная ранняя PCR-политика и изменённый порядок выделения
проверены на физическом Intel TPM через реальную ZBM/kexec загрузку. Запись
результата: `nvpcr-20261007/physical-accepted-0510.json` под
`/var/lib/zbm-secureboot/`. Пройденная приёмка не обещает совместимость с ещё
не установленным корпоративным VPN: его требования отдельно не исследовались.

## Команды для следующей проверки

```sh
uname -r
cat /proc/cmdline
journalctl -k -b --no-pager -g '^Secure boot enabled$'
journalctl -b -u zbm-audit-import.service --no-pager
sudo cat /sys/kernel/security/lockdown
sudo keyctl list %:.ima
sudo keyctl list %:.builtin_trusted_keys
sudo zpool status -x
modinfo -F signer amneziawg
systemctl --failed --no-pager
journalctl -b -u systemd-tpm2-setup-early.service -u systemd-tpm2-setup.service --no-pager
journalctl -k -b -g insyde_efivarfs --no-pager
journalctl -b -u systemd-modules-load.service --no-pager
systemctl show systemd-tpm2-setup-early.service systemd-tpm2-setup.service -p ConditionResult -p Result -p ExecMainStatus
udevadm info --query=property --name=/dev/tpmrm0
sudo systemd-analyze nvpcrs --no-pager
journalctl -b -u systemd-pcrnvdone.service -u systemd-pcrproduct.service -u systemd-pcrlogin@1000.service --no-pager
```

`sbctl status` и `bootctl status` после восстановления видят реальное состояние
firmware. Для проверки файлов ESP нужен смонтированный ESP; `--esp-path=/mnt/efi`
применим после его монтирования туда. Если unsupported/non-UEFI вернётся до recovery,
нужно сопоставить вывод с логами ядра и проверить service/module. Не нужно повторно
создавать или очищать ключи, чтобы «исправить» такой вывод.

## Источники и границы следующей работы

- [Linux IMA policy write/read implementation](https://github.com/torvalds/linux/blob/master/security/integrity/ima/ima_fs.c)
- [Linux IMA policy rules](https://github.com/torvalds/linux/blob/master/security/integrity/ima/ima_policy.c)
- [OpenZFS interactive key retry implementation](https://github.com/openzfs/zfs/blob/master/lib/libzfs/libzfs_crypto.c)
- [sbctl enrollment/export documentation](https://github.com/Foxboron/sbctl/blob/master/docs/sbctl.8.txt)
- [Наблюдения Core Ultra по очистке Secure Boot/DBX](https://community.frame.work/t/secureboot-setup-mode/14889/25)
- [Framework issue #76: перечисление EFI variables, включая Core Ultra BIOS 3.07](https://github.com/FrameworkComputer/SoftwareFirmwareIssueTracker/issues/76)
- [Статический разбор дефекта в Framework BIOS и ограничения его проверки](https://github.com/melynx/insyde-uefi-variable-recovery/blob/master/docs/framework-follow-up.md)

Следующие отдельные задачи: проверка BIOS-password status владельцем и развитие
более строгой архитектуры zbm-rs. Нынешняя успешная загрузка не заменяет эти проверки
и не превращает будущий дизайн в уже установленную реализацию.
