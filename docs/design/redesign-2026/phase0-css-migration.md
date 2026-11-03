# Phase 0 CSS migration map

Original line numbers refer to main:frontend/src/styles/layout.css. Shared selector groups were separated by ownership; declaration order and media conditions were retained. Shell rules are subsequently redesigned.

| Original lines | Destination under frontend/src/styles | Media | Selectors |
|---|---|---|---|
| 3–7 | shell.css | `all` | `.app-shell` |
| 9–19 | shell.css | `all` | `.app-sidebar` |
| 21–26 | shell.css | `all` | `.sidebar-brand, .customer-brand` |
| 28–30 | shell.css | `all` | `.sidebar-brand` |
| 32–34 | shell.css | `all` | `.sidebar-brand:hover` |
| 36–38 | shell.css | `all` | `.sidebar-brand div, .customer-brand div` |
| 40–49 | shell.css | `all` | `.brand-mark, .customer-brand > span` |
| 51–54 | shell.css | `all` | `.sidebar-nav` |
| 56–64 | shell.css | `all` | `.sidebar-nav a` |
| 66–69 | shell.css | `all` | `.sidebar-nav a:hover` |
| 71–74 | shell.css | `all` | `.sidebar-nav a.active` |
| 76–84 | shell.css | `all` | `.sidebar-runtime` |
| 86–91 | shell.css | `all` | `.sidebar-support` |
| 93–95 | shell.css | `all` | `.sidebar-runtime small` |
| 97–99 | shell.css | `all` | `.runtime-offline, .runtime-degraded` |
| 101–103 | shell.css | `all` | `.runtime-healthy svg` |
| 105–111 | shell.css | `all` | `.sidebar-account` |
| 113–121 | shell.css | `all` | `.account-avatar` |
| 123–127 | shell.css | `all` | `.sidebar-account > div` |
| 129–133 | shell.css | `all` | `.sidebar-account strong` |
| 135–137 | shell.css | `all` | `.app-main` |
| 139–147 | shell.css | `all` | `.app-topbar` |
| 149–152 | shell.css | `all` | `.app-topbar > div` |
| 154–157 | shell.css | `all` | `.app-topbar h1` |
| 159–163 | shell.css | `all` | `.app-topbar p` |
| 165–168 | shell.css | `all` | `.topbar-support` |
| 170–174 | shell.css | `all` | `.app-content` |
| 176–178 | shell.css | `all` | `.mobile-menu` |
| 176–178 | pages/workspace.css | `all` | `.mobile-detail-back` |
| 180–187 | pages/workspace.css | `all` | `.workspace-summary` |
| 189–191 | pages/workspace.css | `all` | `.workspace-summary p` |
| 193–195 | pages/workspace.css | `all` | `.workspace-summary > span` |
| 197–202 | pages/workspace.css | `all` | `.grid-dashboard` |
| 204–206 | pages/workspace.css | `all` | `.workspace-detail` |
| 208–213 | pages/workspace.css | `all` | `.ticket-queue, .ticket-detail, .ticket-empty-state` |
| 208–213 | pages/observability.css | `all` | `.obs-runs-card` |
| 208–213 | pages/resources.css | `all` | `.resource-section` |
| 208–213 | shell.css | `all` | `.staff-login-card` |
| 215–218 | pages/workspace.css | `all` | `.queue-heading` |
| 220–223 | shell.css | `all` | `.section-label` |
| 225–227 | pages/workspace.css | `all` | `.queue-heading .section-label` |
| 229–232 | pages/workspace.css | `all` | `.queue-heading h2` |
| 234–239 | pages/workspace.css | `all` | `.queue-heading em` |
| 241–247 | pages/workspace.css | `all` | `.queue-filters` |
| 249–254 | pages/workspace.css | `all` | `.queue-search` |
| 256–260 | pages/workspace.css | `all` | `.queue-search svg` |
| 262–265 | pages/workspace.css | `all` | `.queue-search input` |
| 267–271 | pages/workspace.css | `all` | `.queue-filters select` |
| 273–281 | pages/workspace.css | `all` | `.queue-filter-summary` |
| 283–288 | pages/workspace.css | `all` | `.queue-filter-summary button` |
| 290–292 | pages/workspace.css | `all` | `.queue-filter-summary button:disabled` |
| 294–298 | pages/workspace.css | `all` | `.ticket-list` |
| 300–303 | pages/workspace.css | `all` | `.queue-empty` |
| 305–315 | pages/workspace.css | `all` | `.ticket-row` |
| 317–319 | pages/workspace.css | `all` | `.ticket-row:hover` |
| 321–324 | pages/workspace.css | `all` | `.ticket-row.selected` |
| 326–331 | pages/workspace.css | `all` | `.ticket-row-top, .ticket-row-bottom, .ticket-row-meta, .ticket-row-heading` |
| 333–335 | pages/workspace.css | `all` | `.ticket-row-top > span:first-child` |
| 337–340 | pages/workspace.css | `all` | `.ticket-row h3` |
| 342–350 | pages/workspace.css | `all` | `.ticket-row p` |
| 352–355 | pages/workspace.css | `all` | `.ticket-row small, .ticket-row-id, .ticket-row-time, .ticket-customer, .ticket-meta` |
| 357–361 | pages/workspace.css | `all` | `.ticket-row` |
| 363–365 | pages/workspace.css | `all` | `.priority-rail, .status-dot` |
| 367–372 | pages/workspace.css | `all` | `.ticket-row-body` |
| 374–381 | pages/workspace.css | `all` | `.ticket-row-preview` |
| 383–389 | pages/workspace.css | `all` | `.ticket-row-footer` |
| 391–395 | pages/workspace.css | `all` | `.ticket-row-footer > span` |
| 397–399 | pages/workspace.css | `all` | `.priority-text-high, .priority-text-urgent` |
| 401–404 | pages/workspace.css | `all` | `.ticket-row-arrow` |
| 406–411 | pages/workspace.css | `all` | `.queue-footer` |
| 413–418 | pages/resources.css | `all` | `.resource-search` |
| 420–423 | pages/resources.css | `all` | `.tool-table td:first-child` |
| 425–429 | pages/resources.css | `all` | `.tool-table td p` |
| 431–433 | pages/resources.css | `all` | `.tool-table td:not(:first-child)` |
| 435–440 | pages/workspace.css | `all` | `.tool-review-loading` |
| 442–448 | pages/workspace.css | `all` | `.saved-result-notice` |
| 450–456 | pages/resources.css | `all` | `.resource-form-fields` |
| 458–466 | pages/workspace.css | `all` | `.ticket-empty-state` |
| 468–471 | pages/workspace.css | `all` | `.empty-state-icon` |
| 473–476 | pages/workspace.css | `all` | `.ticket-empty-state p` |
| 478–484 | pages/workspace.css | `all` | `.empty-workflow` |
| 486–493 | pages/workspace.css | `all` | `.ticket-detail-header` |
| 495–498 | pages/workspace.css | `all` | `.ticket-detail-header > div` |
| 500–507 | pages/workspace.css | `all` | `.ticket-detail-meta` |
| 509–511 | pages/workspace.css | `all` | `.ticket-detail-header h2` |
| 513–517 | pages/workspace.css | `all` | `.ticket-detail-header p` |
| 519–528 | pages/workspace.css | `all` | `.workflow-progress` |
| 530–534 | pages/workspace.css | `all` | `.workflow-progress span` |
| 536–542 | pages/workspace.css | `all` | `.workflow-progress i` |
| 544–549 | pages/workspace.css | `all` | `.workflow-progress b` |
| 551–553 | pages/workspace.css | `all` | `.workflow-progress .active` |
| 555–560 | pages/workspace.css | `all` | `.case-context-grid` |
| 562–567 | pages/workspace.css | `all` | `.detail-card` |
| 569–574 | pages/workspace.css | `all` | `.card-heading, .assistant-title` |
| 576–578 | pages/workspace.css | `all` | `.card-heading > div, .assistant-title > div` |
| 580–582 | pages/workspace.css | `all` | `.card-heading span` |
| 584–586 | pages/workspace.css | `all` | `.card-icon, .assistant-logo` |
| 588–592 | pages/workspace.css | `all` | `.issue-card p` |
| 594–597 | pages/workspace.css | `all` | `.customer-facts` |
| 599–603 | pages/workspace.css | `all` | `.customer-facts > div` |
| 605–608 | pages/workspace.css | `all` | `.customer-facts span` |
| 610–612 | pages/workspace.css | `all` | `.orders-disclosure` |
| 614–620 | pages/workspace.css | `all` | `.orders-disclosure summary, .reference-disclosure summary` |
| 622–626 | pages/workspace.css | `all` | `.orders-disclosure summary > span, .reference-disclosure summary > span` |
| 628–633 | pages/workspace.css | `all` | `.orders-grid` |
| 635–639 | pages/workspace.css | `all` | `.order-item` |
| 641–646 | pages/workspace.css | `all` | `.order-item > div` |
| 648–651 | pages/workspace.css | `all` | `.order-item p` |
| 653–658 | pages/workspace.css | `all` | `.agent-loading-card, .detail-error` |
| 660–663 | pages/workspace.css | `all` | `.agent-loading-card p` |
| 665–667 | pages/workspace.css | `all` | `.detail-error` |
| 669–671 | pages/workspace.css | `all` | `.detail-error span` |
| 673–675 | pages/workspace.css | `all` | `.loading-dots` |
| 677–679 | pages/workspace.css | `all` | `.assistant-panel` |
| 681–688 | pages/workspace.css | `all` | `.assistant-heading` |
| 690–692 | pages/workspace.css | `all` | `.assistant-title` |
| 694–696 | pages/workspace.css | `all` | `.assistant-title span` |
| 698–702 | pages/workspace.css | `all` | `.assistant-badges` |
| 704–714 | pages/workspace.css | `all` | `.escalation-banner` |
| 716–719 | pages/workspace.css | `all` | `.escalation-banner div` |
| 721–723 | pages/workspace.css | `all` | `.escalation-banner small` |
| 725–728 | pages/workspace.css | `all` | `.escalation-reasons` |
| 730–732 | pages/workspace.css | `all` | `.draft-section` |
| 734–740 | pages/workspace.css | `all` | `.draft-label` |
| 742–745 | pages/workspace.css | `all` | `.draft-label > div` |
| 747–750 | pages/workspace.css | `all` | `.draft-label span` |
| 752–755 | pages/workspace.css | `all` | `.draft-section textarea` |
| 757–759 | pages/workspace.css | `all` | `.reference-disclosure` |
| 761–765 | pages/workspace.css | `all` | `.reference-list` |
| 767–771 | pages/workspace.css | `all` | `.reference-item` |
| 773–778 | pages/workspace.css | `all` | `.reference-item > div` |
| 780–783 | pages/workspace.css | `all` | `.reference-item em` |
| 785–788 | pages/workspace.css | `all` | `.reference-item p` |
| 790–798 | pages/workspace.css | `all` | `.assistant-actions` |
| 800–804 | pages/workspace.css | `all` | `.assistant-actions > div` |
| 806–811 | pages/workspace.css | `all` | `.no-review-needed` |
| 813–815 | pages/workspace.css | `all` | `.action-notice` |
| 817–822 | pages/workspace.css | `all` | `.evaluation-panel` |
| 824–827 | pages/workspace.css | `all` | `.evaluation-heading` |
| 829–831 | pages/workspace.css | `all` | `.evaluation-heading > div` |
| 833–838 | pages/workspace.css | `all` | `.evaluation-grid` |
| 840–842 | pages/workspace.css | `all` | `.evaluation-grid > div` |
| 844–847 | pages/workspace.css | `all` | `.evaluation-grid span` |
| 849–851 | pages/workspace.css | `all` | `.score-risk` |
| 853–856 | pages/workspace.css | `all` | `.tool-review-panel` |
| 858–861 | pages/workspace.css | `all` | `.tool-review-item` |
| 863–865 | pages/workspace.css | `all` | `.tool-review-item p` |
| 867–869 | pages/workspace.css | `all` | `.tool-review-item small` |
| 871–875 | pages/workspace.css | `all` | `.tool-review-form` |
| 877–879 | pages/workspace.css | `all` | `.tool-review-error` |
| 883–887 | shell.css | `all` | `.customer-portal` |
| 889–896 | shell.css | `all` | `.customer-header` |
| 898–902 | shell.css | `all` | `.customer-header-actions` |
| 904–914 | shell.css | `all` | `.staff-entry, .back-to-customer` |
| 916–919 | pages/support.css | `all` | `.customer-hero` |
| 921–923 | pages/support.css | `all` | `.customer-hero-copy` |
| 925–929 | pages/support.css | `all` | `.customer-hero-copy p` |
| 931–933 | pages/support.css | `all` | `.customer-eyebrow, .customer-capabilities` |
| 935–939 | pages/support.css | `all` | `.support-card` |
| 941–948 | pages/support.css | `all` | `.support-chat-toolbar` |
| 950–954 | pages/support.css | `all` | `.support-form-heading` |
| 956–959 | pages/support.css | `all` | `.support-form-heading > div` |
| 961–963 | pages/support.css | `all` | `.support-bot` |
| 965–976 | pages/support.css | `all` | `.support-new-session, .support-secondary` |
| 978–985 | pages/support.css | `all` | `.support-customer-selector` |
| 987–991 | pages/support.css | `all` | `.support-customer-selector > span` |
| 993–997 | pages/support.css | `all` | `.support-customer-selector select` |
| 999–1008 | pages/support.css | `all` | `.support-conversation` |
| 1010–1015 | pages/support.css | `all` | `.support-message` |
| 1017–1027 | pages/support.css | `all` | `.support-message > span` |
| 1029–1032 | pages/support.css | `all` | `.support-message > div` |
| 1034–1040 | pages/support.css | `all` | `.support-message p` |
| 1042–1045 | pages/support.css | `all` | `.support-message small` |
| 1047–1050 | pages/support.css | `all` | `.support-message.user` |
| 1052–1056 | pages/support.css | `all` | `.support-message.user > div` |
| 1058–1061 | pages/support.css | `all` | `.support-message.status p` |
| 1063–1066 | pages/support.css | `all` | `.support-message.error p` |
| 1068–1072 | pages/support.css | `all` | `.support-typing p` |
| 1074–1078 | pages/support.css | `all` | `.support-history-day` |
| 1080–1087 | pages/support.css | `all` | `.support-history-loading` |
| 1089–1097 | pages/support.css | `all` | `.support-history-error, .support-status-panel, .support-error, .feedback-success` |
| 1099–1103 | pages/support.css | `all` | `.support-history-error, .support-error` |
| 1105–1107 | pages/support.css | `all` | `.support-history-error span` |
| 1109–1113 | pages/support.css | `all` | `.support-history-error button` |
| 1115–1118 | pages/support.css | `all` | `.support-status-panel` |
| 1120–1123 | pages/support.css | `all` | `.support-status-panel > div` |
| 1125–1127 | pages/support.css | `all` | `.feedback-success` |
| 1129–1134 | pages/support.css | `all` | `.support-feedback` |
| 1136–1139 | pages/support.css | `all` | `.feedback-rating` |
| 1141–1147 | pages/support.css | `all` | `.feedback-rating button` |
| 1149–1152 | pages/support.css | `all` | `.feedback-rating button.active` |
| 1154–1156 | pages/support.css | `all` | `.support-feedback textarea` |
| 1158–1160 | pages/support.css | `all` | `.support-feedback > button` |
| 1162–1164 | pages/support.css | `all` | `.feedback-error` |
| 1166–1172 | pages/support.css | `all` | `.support-composer` |
| 1174–1177 | pages/support.css | `all` | `.question-examples` |
| 1179–1182 | pages/support.css | `all` | `.question-examples > span` |
| 1184–1188 | pages/support.css | `all` | `.question-examples > div` |
| 1190–1198 | pages/support.css | `all` | `.question-examples button` |
| 1200–1202 | pages/support.css | `all` | `.support-message-field` |
| 1204–1206 | pages/support.css | `all` | `.support-message-field > span` |
| 1208–1213 | pages/support.css | `all` | `.support-message-field textarea` |
| 1215–1219 | pages/support.css | `all` | `.support-message-field > small` |
| 1221–1234 | pages/support.css | `all` | `.support-submit` |
| 1236–1241 | pages/support.css | `all` | `.support-privacy` |
| 1243–1246 | pages/support.css | `all` | `.support-privacy svg` |
| 1248–1251 | pages/support.css | `all` | `.support-card > .support-privacy` |
| 1253–1258 | pages/support.css | `all` | `.customer-footer` |
| 1260–1266 | shell.css | `all` | `.staff-login-page` |
| 1268–1271 | shell.css | `all` | `.staff-login-card` |
| 1273–1275 | shell.css | `all` | `.staff-login-heading` |
| 1277–1279 | shell.css | `all` | `.staff-login-heading .brand-mark` |
| 1281–1284 | shell.css | `all` | `.staff-login-heading p` |
| 1286–1289 | shell.css | `all` | `.staff-login-form` |
| 1291–1295 | shell.css | `all` | `.staff-register` |
| 1299–1306 | pages/observability.css | `all` | `.obs-hero` |
| 1299–1306 | pages/resources.css | `all` | `.resource-hero` |
| 1308–1310 | pages/observability.css | `all` | `.obs-hero > div:first-child` |
| 1308–1310 | pages/resources.css | `all` | `.resource-hero > div:first-child` |
| 1312–1314 | pages/observability.css | `all` | `.obs-hero h2` |
| 1312–1314 | pages/resources.css | `all` | `.resource-hero h2` |
| 1316–1319 | pages/observability.css | `all` | `.obs-hero p` |
| 1316–1319 | pages/resources.css | `all` | `.resource-hero p` |
| 1321–1328 | pages/observability.css | `all` | `.obs-eyebrow` |
| 1321–1328 | pages/resources.css | `all` | `.resource-eyebrow` |
| 1330–1333 | pages/observability.css | `all` | `.obs-hero-actions` |
| 1335–1342 | pages/observability.css | `all` | `.obs-summary-grid` |
| 1344–1347 | pages/observability.css | `all` | `.obs-summary-card` |
| 1349–1351 | pages/observability.css | `all` | `.obs-summary-card:last-child` |
| 1353–1355 | pages/observability.css | `all` | `.obs-summary-icon` |
| 1357–1360 | pages/observability.css | `all` | `.obs-summary-label, .obs-summary-hint` |
| 1362–1368 | pages/observability.css | `all` | `.obs-summary-value` |
| 1370–1378 | pages/observability.css | `all` | `.obs-table-heading` |
| 1380–1382 | pages/observability.css | `all` | `.obs-table-heading h3` |
| 1384–1388 | pages/observability.css | `all` | `.obs-table-heading p` |
| 1390–1393 | pages/observability.css | `all` | `.obs-table-controls` |
| 1395–1399 | pages/observability.css | `all` | `.obs-table-controls > span` |
| 1401–1405 | pages/observability.css | `all` | `.obs-ticket-search` |
| 1407–1409 | pages/observability.css | `all` | `.obs-ticket-search input` |
| 1411–1413 | pages/observability.css | `all` | `.obs-table-wrap` |
| 1411–1413 | pages/resources.css | `all` | `.data-table-wrap` |
| 1415–1419 | pages/observability.css | `all` | `.obs-table` |
| 1415–1419 | pages/resources.css | `all` | `.data-table` |
| 1421–1428 | pages/observability.css | `all` | `.obs-table th` |
| 1421–1428 | pages/resources.css | `all` | `.data-table th` |
| 1430–1435 | pages/observability.css | `all` | `.obs-table td` |
| 1430–1435 | pages/resources.css | `all` | `.data-table td` |
| 1437–1439 | pages/observability.css | `all` | `.obs-table tbody tr` |
| 1441–1443 | pages/observability.css | `all` | `.obs-table tbody tr:hover` |
| 1441–1443 | pages/resources.css | `all` | `.data-table tbody tr:hover` |
| 1445–1449 | pages/observability.css | `all` | `.obs-table td small` |
| 1451–1453 | pages/observability.css | `all` | `.obs-table td strong` |
| 1455–1457 | pages/observability.css | `all` | `.obs-table td:first-child` |
| 1459–1465 | pages/observability.css | `all` | `.obs-pagination` |
| 1467–1469 | pages/observability.css | `all` | `.obs-detail-header` |
| 1471–1474 | pages/observability.css | `all` | `.obs-detail-header h2` |
| 1476–1478 | pages/observability.css | `all` | `.obs-detail-content` |
| 1480–1483 | pages/observability.css | `all` | `.obs-detail-section` |
| 1485–1487 | pages/observability.css | `all` | `.obs-detail-section p:last-child` |
| 1489–1498 | pages/observability.css | `all` | `.obs-trace-box` |
| 1500–1503 | pages/observability.css | `all` | `.obs-trace-box code` |
| 1505–1509 | pages/observability.css | `all` | `.obs-helper` |
| 1511–1516 | pages/observability.css | `all` | `.obs-workflow` |
| 1518–1523 | pages/observability.css | `all` | `.obs-node` |
| 1525–1528 | pages/observability.css | `all` | `.obs-node span` |
| 1530–1534 | pages/observability.css | `all` | `.obs-kv-grid, .obs-analyzer-grid` |
| 1536–1539 | pages/observability.css | `all` | `.obs-kv-grid dt, .obs-analyzer-grid dt` |
| 1541–1543 | pages/observability.css | `all` | `.obs-kv-grid dd` |
| 1545–1550 | pages/observability.css | `all` | `.obs-analyzer-snapshot` |
| 1552–1557 | pages/observability.css | `all` | `.obs-snapshot-heading` |
| 1559–1561 | pages/observability.css | `all` | `.obs-snapshot-heading > div` |
| 1563–1566 | pages/observability.css | `all` | `.obs-snapshot-heading span` |
| 1568–1571 | pages/observability.css | `all` | `.obs-compact-list` |
| 1573–1579 | pages/observability.css | `all` | `.obs-compact-list > div` |
| 1581–1584 | pages/observability.css | `all` | `.obs-compact-list span` |
| 1586–1590 | pages/observability.css | `all` | `.obs-compact-list em` |
| 1592–1594 | pages/observability.css | `all` | `.obs-error-section` |
| 1596–1598 | pages/observability.css | `all` | `.obs-text-snapshot` |
| 1600–1607 | pages/observability.css | `all` | `.obs-text-snapshot p` |
| 1609–1614 | pages/resources.css | `all` | `.resource-tabs` |
| 1616–1625 | pages/resources.css | `all` | `.resource-tabs button` |
| 1627–1630 | pages/resources.css | `all` | `.resource-tabs button.active` |
| 1632–1634 | pages/resources.css | `all` | `.resource-tabs span` |
| 1636–1638 | pages/resources.css | `all` | `.resource-section` |
| 1640–1646 | pages/resources.css | `all` | `.resource-stat-row` |
| 1648–1651 | pages/resources.css | `all` | `.resource-stat-row > div` |
| 1653–1655 | pages/resources.css | `all` | `.resource-stat-row span` |
| 1657–1664 | pages/resources.css | `all` | `.resource-section-heading` |
| 1666–1669 | pages/resources.css | `all` | `.resource-section-heading > div` |
| 1671–1675 | pages/resources.css | `all` | `.resource-section-heading p` |
| 1677–1679 | pages/resources.css | `all` | `.tool-admin-grid` |
| 1681–1687 | pages/resources.css | `all` | `.tool-admin-card` |
| 1689–1691 | pages/resources.css | `all` | `.tool-admin-card:last-child` |
| 1693–1695 | pages/resources.css | `all` | `.tool-admin-card > details` |
| 1697–1703 | pages/resources.css | `all` | `.tool-admin-heading` |
| 1705–1708 | pages/resources.css | `all` | `.tool-admin-heading p` |
| 1710–1716 | pages/resources.css | `all` | `.tool-admin-tags` |
| 1718–1721 | pages/resources.css | `all` | `.tool-admin-tags span` |
| 1723–1727 | pages/resources.css | `all` | `.tool-disabled-reason` |
| 1729–1731 | pages/resources.css | `all` | `.tool-admin-card > button` |
| 1733–1737 | pages/resources.css | `all` | `.prompt-admin-layout` |
| 1739–1741 | pages/resources.css | `all` | `.prompt-admin-main` |
| 1743–1748 | pages/resources.css | `all` | `.prompt-environments` |
| 1750–1756 | pages/resources.css | `all` | `.prompt-environments > div` |
| 1758–1761 | pages/resources.css | `all` | `.prompt-environments span` |
| 1763–1766 | pages/resources.css | `all` | `.prompt-bundle-list` |
| 1768–1773 | pages/resources.css | `all` | `.prompt-bundle-list summary` |
| 1775–1777 | pages/resources.css | `all` | `.prompt-bundle-list summary > div` |
| 1779–1782 | pages/resources.css | `all` | `.prompt-bundle-list summary > span` |
| 1784–1789 | pages/resources.css | `all` | `.prompt-governance-card` |
| 1791–1795 | pages/resources.css | `all` | `.prompt-governance-card p` |
| 1797–1803 | pages/resources.css | `all` | `.prompt-editor` |
| 1805–1808 | pages/resources.css | `all` | `.prompt-editor fieldset` |
| 1810–1812 | pages/resources.css | `all` | `.prompt-editor textarea` |
| 1814–1819 | pages/resources.css | `all` | `.resource-form-actions` |
| 1821–1825 | pages/resources.css | `all` | `.rag-admin-layout` |
| 1827–1831 | pages/resources.css | `all` | `.rag-document-list > article` |
| 1833–1841 | pages/resources.css | `all` | `.rag-document-main` |
| 1843–1847 | pages/resources.css | `all` | `.rag-document-main > span, .rag-document-main > p` |
| 1849–1852 | pages/resources.css | `all` | `.rag-document-main > div` |
| 1854–1860 | pages/resources.css | `all` | `.rag-document-main em` |
| 1862–1864 | pages/resources.css | `all` | `.rag-document-list article.selected .rag-document-main strong` |
| 1866–1878 | pages/resources.css | `all` | `.rag-delete` |
| 1880–1886 | pages/resources.css | `all` | `.rag-document-editor` |
| 1888–1892 | pages/resources.css | `all` | `.rag-form-grid` |
| 1894–1899 | pages/workspace.css | `all` | `.metrics-strip` |
| 1901–1905 | pages/workspace.css | `all` | `.metric-item` |
| 1907–1909 | pages/workspace.css | `all` | `.metric-item > div` |
| 1911–1914 | pages/workspace.css | `all` | `.metric-item span` |
| 1916–1918 | pages/workspace.css | `all` | `.metric-item strong` |
| 1921–1924 | pages/workspace.css | `@media (max-width: 1279px)` | `.grid-dashboard` |
| 1925–1927 | pages/workspace.css | `@media (max-width: 1279px)` | `.case-context-grid` |
| 1928–1930 | pages/resources.css | `@media (max-width: 1279px)` | `.prompt-admin-layout` |
| 1931–1933 | pages/resources.css | `@media (max-width: 1279px)` | `.prompt-governance-card` |
| 1934–1936 | pages/resources.css | `@media (max-width: 1279px)` | `.rag-admin-layout` |
| 1937–1941 | pages/resources.css | `@media (max-width: 1279px)` | `.rag-document-editor` |
| 1945–1947 | shell.css | `@media (max-width: 1023px)` | `.app-shell` |
| 1948–1950 | pages/workspace.css | `@media (max-width: 1023px)` | `.grid-dashboard` |
| 1951–1953 | pages/workspace.css | `@media (max-width: 1023px)` | `.workspace-page.has-selection .ticket-queue` |
| 1954–1956 | pages/workspace.css | `@media (max-width: 1023px)` | `.workspace-page:not(.has-selection) .workspace-detail` |
| 1957–1960 | pages/workspace.css | `@media (max-width: 1023px)` | `.mobile-detail-back` |
| 1961–1963 | pages/workspace.css | `@media (max-width: 1023px)` | `.ticket-list` |
| 1964–1966 | pages/observability.css | `@media (max-width: 1023px)` | `.obs-summary-grid` |
| 1967–1969 | pages/observability.css | `@media (max-width: 1023px)` | `.obs-summary-card:nth-child(2)` |
| 1970–1972 | pages/observability.css | `@media (max-width: 1023px)` | `.obs-summary-card:nth-child(-n+2)` |
| 1973–1975 | shell.css | `@media (max-width: 1023px)` | `.topbar-support` |
| 1979–1981 | shell.css | `@media (max-width: 767px)` | `.app-shell` |
| 1982–1991 | shell.css | `@media (max-width: 767px)` | `.app-sidebar` |
| 1992–1994 | shell.css | `@media (max-width: 767px)` | `.nav-open .app-sidebar` |
| 1995–1997 | shell.css | `@media (max-width: 767px)` | `.sidebar-runtime` |
| 1998–2000 | shell.css | `@media (max-width: 767px)` | `.sidebar-account` |
| 2001–2005 | shell.css | `@media (max-width: 767px)` | `.app-topbar` |
| 2006–2008 | shell.css | `@media (max-width: 767px)` | `.app-topbar h1` |
| 2009–2011 | shell.css | `@media (max-width: 767px)` | `.app-topbar p` |
| 2012–2014 | shell.css | `@media (max-width: 767px)` | `.app-topbar > .btn > span` |
| 2015–2017 | shell.css | `@media (max-width: 767px)` | `.mobile-menu` |
| 2018–2020 | shell.css | `@media (max-width: 767px)` | `.app-content` |
| 2021–2023 | pages/workspace.css | `@media (max-width: 767px)` | `.case-context-grid, .ticket-detail-header, .assistant-heading` |
| 2024–2026 | pages/workspace.css | `@media (max-width: 767px)` | `.draft-section` |
| 2027–2029 | pages/workspace.css | `@media (max-width: 767px)` | `.reference-disclosure, .orders-disclosure, .escalation-banner` |
| 2030–2032 | pages/workspace.css | `@media (max-width: 767px)` | `.assistant-actions` |
| 2033–2035 | pages/workspace.css | `@media (max-width: 767px)` | `.assistant-actions .btn` |
| 2036–2038 | pages/workspace.css | `@media (max-width: 767px)` | `.orders-grid` |
| 2036–2038 | pages/resources.css | `@media (max-width: 767px)` | `.rag-form-grid` |
| 2039–2041 | pages/observability.css | `@media (max-width: 767px)` | `.obs-hero-actions` |
| 2042–2044 | pages/observability.css | `@media (max-width: 767px)` | `.obs-table-heading` |
| 2045–2047 | pages/observability.css | `@media (max-width: 767px)` | `.obs-ticket-search` |
| 2048–2050 | pages/observability.css | `@media (max-width: 767px)` | `.obs-ticket-search input` |
| 2051–2053 | pages/observability.css | `@media (max-width: 767px)` | `.obs-table-controls` |
| 2054–2056 | pages/observability.css | `@media (max-width: 767px)` | `.obs-table-controls > span` |
| 2057–2060 | pages/observability.css | `@media (max-width: 767px)` | `.obs-detail-content, .obs-detail-header` |
| 2061–2063 | pages/observability.css | `@media (max-width: 767px)` | `.obs-kv-grid, .obs-analyzer-grid` |
| 2064–2066 | pages/resources.css | `@media (max-width: 767px)` | `.resource-section` |
| 2067–2070 | pages/resources.css | `@media (max-width: 767px)` | `.resource-tabs` |
| 2071–2075 | pages/resources.css | `@media (max-width: 767px)` | `.resource-tabs button` |
| 2076–2078 | pages/resources.css | `@media (max-width: 767px)` | `.resource-tabs span` |
| 2079–2082 | pages/resources.css | `@media (max-width: 767px)` | `.resource-stat-row` |
| 2083–2085 | pages/resources.css | `@media (max-width: 767px)` | `.tool-admin-card` |
| 2086–2088 | pages/resources.css | `@media (max-width: 767px)` | `.tool-admin-card > button` |
| 2089–2091 | pages/resources.css | `@media (max-width: 767px)` | `.prompt-environments` |
| 2092–2094 | shell.css | `@media (max-width: 767px)` | `.customer-portal` |
| 2095–2101 | shell.css | `@media (max-width: 767px)` | `.customer-header` |
| 2102–2104 | shell.css | `@media (max-width: 767px)` | `.customer-brand small` |
| 2105–2107 | shell.css | `@media (max-width: 767px)` | `.customer-header-actions` |
| 2108–2111 | shell.css | `@media (max-width: 767px)` | `.customer-header-actions .staff-entry` |
| 2112–2114 | shell.css | `@media (max-width: 767px)` | `.customer-header-actions .staff-entry > svg` |
| 2115–2117 | pages/support.css | `@media (max-width: 767px)` | `.customer-hero` |
| 2118–2120 | pages/support.css | `@media (max-width: 767px)` | `.customer-hero-copy h1` |
| 2121–2124 | pages/support.css | `@media (max-width: 767px)` | `.support-chat-toolbar` |
| 2125–2127 | pages/support.css | `@media (max-width: 767px)` | `.support-bot` |
| 2128–2130 | pages/support.css | `@media (max-width: 767px)` | `.support-form-heading strong` |
| 2131–2133 | pages/support.css | `@media (max-width: 767px)` | `.support-form-heading small` |
| 2134–2136 | pages/support.css | `@media (max-width: 767px)` | `.support-new-session` |
| 2137–2141 | pages/support.css | `@media (max-width: 767px)` | `.support-customer-selector` |
| 2142–2145 | pages/support.css | `@media (max-width: 767px)` | `.support-customer-selector select` |
| 2146–2150 | pages/support.css | `@media (max-width: 767px)` | `.support-conversation` |
| 2151–2154 | pages/support.css | `@media (max-width: 767px)` | `.support-message` |
| 2155–2157 | pages/support.css | `@media (max-width: 767px)` | `.support-message > span` |
| 2158–2160 | pages/support.css | `@media (max-width: 767px)` | `.support-message.user > div` |
| 2161–2163 | pages/support.css | `@media (max-width: 767px)` | `.support-composer` |
| 2164–2167 | pages/support.css | `@media (max-width: 767px)` | `.support-submit` |
| 2168–2170 | pages/support.css | `@media (max-width: 767px)` | `.support-feedback` |
| 2171–2173 | pages/support.css | `@media (max-width: 767px)` | `.support-history-error, .support-status-panel, .support-error, .feedback-success` |
| 2174–2176 | shell.css | `@media (max-width: 767px)` | `.staff-login-card` |
| 2177–2179 | pages/support.css | `@media (max-width: 767px)` | `.question-examples > div` |
| 2180–2182 | pages/workspace.css | `@media (max-width: 767px)` | `.ticket-empty-state` |
| 2183–2185 | pages/workspace.css | `@media (max-width: 767px)` | `.metrics-strip` |
