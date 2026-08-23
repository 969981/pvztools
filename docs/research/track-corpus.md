# Research: track-corpus (2026-08-22, agent-gathered; verify before relying on)

## Access notes (affects verbatim-ness)
- wiki.pvz1.com (124.156.143.94) and forum.pvz1.com refused/timed out from this host for the whole session. ALL wiki strings below were read from Wayback Machine snapshots of the wiki (`https://web.archive.org/web/<ts>id_/<url>`), HTML stripped with a tag-regex; strings are copied verbatim from that text (fullwidth punctuation preserved where present). `src` gives the ORIGINAL wiki URL; `arch` gives the snapshot timestamp used.
- bilibili cv13534815 text was taken from the article-view API JSON already present in the scratchpad (`cv.json`, `data.id == 13534815`, title "节奏命名体系修正方案（2021/10/11）"); its 常用节奏一览 table is identical to the wiki mirror `攻略:节奏命名体系修正方案` except the wiki mirror adds 5 extra C-series rows (marked below).
- Zhihu p/45182429, p/379738079, p/379924798 → HTTP 403 and no Wayback capture; forum 轨道读本v3.1 → unreachable, no capture. Not included (see unknowns).

## Notation facts observed in the corpus (verbatim-supported)
- Wave separator `|`; some writers wrap the whole track in leading/trailing `|` (`C2：|I-B-PA'A|I-B-Nd|`).
- `-` = "followed after a delay" inside a wave (I-P = ice then cannon; IP-PP = ice + cannon, then later PP). Some FE pages use `+` instead of `-` (`I+Pd|I+Pd|A'+Nd`, `PP|I+PP|I+PP|N`).
- Lengths: either in parentheses `(9,9,9,9)` / `(6|6|6|6|6|6)` / fullwidth `（6，6，6，6，6，6）`, or bare after a space `1976|1976|750|749` (AvZ), or `1738|1738` style. Unit is seconds when numbers are small (≤ ~25, may have decimals 11.5/17.5/16.2/20.3) and centiseconds when 3–4 digits (601, 746, 1437, 1738, 1975, 2030). 修正方案 states explicitly: 白昼I系节奏 lengths are in 厘秒, all others in 秒. Qualifiers inside lengths occur: `(7.5,25-,20+)`, `(20+|15+|6|9)`, `(25|9|9|9|)` (trailing empty slot), `(1361×3)`, `(12s)`, `(1560连续冰波)`.
- `/` = upper-half-field ops / lower-half-field ops within a wave (explicitly stated on 技术:节奏体系: "用了 / 符号分隔上下半场"). Examples: `PD/PSSS`, `Pcc/PSD`, `BI-PDD/P`, `IP-Pd/PD`, `I-PSD/A`, `Ice3PD/I-cc-PPd`.
- `'` (apostrophe / sometimes `’` or backtick) marks an alternate plant: `A'`=辣椒 (but me_神之六炮 page says there `A'`=复制樱桃 and `A₁`=辣椒), `N'`=模仿者核武 (stated verbatim: "我们约定用N'表示模仿者核武"), `I'`=复制冰, `P'`=?(appears in ne_独木枝 `BCCP'A`, meaning not stated on page), `b'` likewise. pe_居中树八炮 uses backtick: `N|N\``.
- Lowercase = weaker/variant: `a`=窝瓜, `d`=尾炸/delayed D (`PPdd` 尾炸 vs `PPDD`), `c`=cannon used as filler/'垫'? (appears as `PPcccc`, `PDDcccc`, `Pcc`, `Ic-PP`, `cPP`, `IP-c-PP`), `C` uppercase also appears (`IC-PP`, `A'C`, `BCCNA'`, `PdC`). `i` lowercase appears once (`IPa+iPa`). `io` = natural-output plants used as activation (`I-io`, `a-io`).
- ICE3 written as `I3` (`I3PD-P`, `I3PPDD-PP`, `I3PP-PPdd`) and also spelled `Ice3` (`Ice3PD/I-cc-PPd`); `I0P` appears (`I0P-B-PPDd`, `I0P-ccP/PD`) = ice0/immediate-ice variant (meaning not stated). `PPI1` (ice1) appears on pe_新二十炮.
- `~` appears in an old free-form track: `A₁Aa~PP` ("~ 表示可能意外刷新可能不刷").
- `u` suffix = unequal wave lengths (`P5u`, `ch4u`, `ch5u`, `P6u`); `*` = lengthened (`ch4*`, `C6i*`, `C7u*`); `-xI`/`-xN`/`-io`/`-xxxx` suffixes; 对/邻/间 prefixes; lowercase rhythm names also occur (`c5u:`, `c4u:`, `c6u`). Colons: ASCII `:` and fullwidth `：` both used (`P4：PP|PP|PP|PA`, `ch4-N：I-PP|...`, `邻c6u：I+PP|...`).
- Non-cyclic, per-wave notation: `w1:c(二路)PSD/PDD` … `w19:PSD/PSD` (me_二十炮); `ch6` used as 'TAS key-control solution' etc. Parenthetical Chinese annotations inside tracks occur: `I'(二路)A'C`, `B-PD(连拦)/PD`, `N(a)`, `PPN(AD)`, `IBb-PA(N)`, `PA（我就要先樱桃！）`.

## Corpus (JSON-like; `len_unit`: s=seconds, cs=centiseconds, none=no lengths)
```json
[
{"id":1,"rhythm":"P4","track":"P4:PP|PP|PP|PP(9,9,9,9)","lineup":"PE. 经典八炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_经典八炮","arch":"2025 (snapshot via web.archive.org/web/2025id_/)"},
{"id":2,"rhythm":"S5","track":"S5:I-PP|PP|PP|PP(18,6,6,6)","lineup":"PE. 经典八炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_经典八炮"},
{"id":3,"rhythm":"ch6","track":"ch6:I-PP|PP|I-PP|PP(11.5,6,11.5,6)","lineup":"PE. 经典八炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_经典八炮"},
{"id":4,"rhythm":"C8u","track":"C8u:I-PP|I-PP|PP|PP|N|PP(13,13,6,6,6,6)","lineup":"PE. 经典八炮 / FE. 经典八炮","scene":"PE,FE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_经典八炮","arch":"20241112234358"},
{"id":5,"rhythm":"P6-2I","track":"P6-2I: PPDD|PPI|PPSSDD|PPDD|PPI|PPSSDD (6,6,6,6,6,6)","lineup":"PE. 二十四炮 (键控解 '完美公式')","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_二十四炮","arch":"20240413132654"},
{"id":6,"rhythm":"对D6 (对ch6)","track":"IPP-PPDD|PPSSDD|IPP-PPDD|PPSSDD (12,6,12,6)","lineup":"PE. 二十四炮 (手控)","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_二十四炮"},
{"id":7,"rhythm":"邻D6","track":"IPP-PPDD|IPP-PPDD|PPSSDD|PPSSDD (12,12,6,6)","lineup":"PE. 二十四炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_二十四炮"},
{"id":8,"rhythm":"S6-c","track":"IPP-PPDD|PPSSDD|PPI|PPSSDD|PPDDcccc (12,6,6,6,6)","lineup":"PE. 二十四炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_二十四炮"},
{"id":9,"rhythm":"P6-3I","track":"PPSSDD|PPI|PPSSDD|PPI|PPSSDD|PPI (6,6,6,6,6,6)","lineup":"PE. 二十四炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_二十四炮"},
{"id":10,"rhythm":"S6-N","track":"IPP-PPDD|PPNDD|PPSSDD|PPI|PPSSDD (12,6,6,6,6)","lineup":"PE. 二十四炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_二十四炮"},
{"id":11,"rhythm":"P6","track":"P6: PP|PP|PPSS|PP|PP|PPSS (6,6,6,6,6,6)","lineup":"PE. 纯爷们十六炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=攻略:复合运算","arch":"20240612175659","note":"lineup page itself writes 'P6: PP|PP|PPDD|PP|PP|PPDD （6,6,6,6,6,6）' (fullwidth '（')"},
{"id":12,"rhythm":"P6","track":"P6: PPDD|PPDD|PPI|PPDD|PPDD|PPI (6,6,6,6,6,6)","lineup":"PE. 无冰瓜二十炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=攻略:复合运算","note":"lineup page writes 'P6-2I：PPDD|PPDD|PPI|PPDD|PPDD|PPI（6,6,6,6,6,6）' (fullwidth colon)"},
{"id":13,"rhythm":"S6","track":"S6: PPDD|PPDD|PPDD|PPDD|IPP-PP (6,6,6,6,12)","lineup":"PE. 无冰瓜二十炮 / PE. 新二十炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_无冰瓜二十炮","arch":"20250806094730"},
{"id":14,"rhythm":"对ch6","track":"对ch6: PPSSDD|IPP-PP|PPSSDD|IPP-PP (6,12,6,12)","lineup":"PE. 无冰瓜二十炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_无冰瓜二十炮"},
{"id":15,"rhythm":"P6u (BD)","track":"P6: PP|BBDD|PP|BBDD|PP|BBDD (6,6,6,6,6,6)","lineup":"PE. 经典十八炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=攻略:复合运算"},
{"id":16,"rhythm":"P6u","track":"P6u: PP|BBDD|PPDD|PP|BBDD|PPDD (6,6,6,6,6,6)","lineup":"PE. 新二十炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_新二十炮","arch":"20250914120433"},
{"id":17,"rhythm":"P6-2I (ice1 written I1)","track":"P6-2I：PPDD|PPDD|PPI1|PPDD|PPDD|PPI1 （6，6，6，6，6，6）","lineup":"PE. 新二十炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_新二十炮"},
{"id":18,"rhythm":"P6","track":"P6: PP|BBDD|PPI|PP|N|BBDD (6,6,6,6,6,6)","lineup":"DE. 十四炮","scene":"DE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=攻略:复合运算"},
{"id":19,"rhythm":"P5u","track":"P5u: PPDD|PPdd|PPDD|PPdd|PPDD (657,752,657,752,657)","lineup":"PE. 纯二十炮","scene":"PE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=攻略:复合运算"},
{"id":20,"rhythm":"P5u","track":"P5u: PD|Pd|PD|Pd|PD, (657|752|657|752|657)","lineup":"PE纯20炮","scene":"PE","len_unit":"cs","src":"https://www.bilibili.com/read/cv13534815"},
{"id":21,"rhythm":"P4","track":"P4：PP|PP|PP|PA （9，9，9，9）","lineup":"DE. 樱桃七炮","scene":"DE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:de_樱桃七炮","arch":"20250113141249"},
{"id":22,"rhythm":"P5","track":"P5:PP|PP|PA|PP|N （7，7，7，7，7）","lineup":"DE. 樱桃七炮","scene":"DE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:de_樱桃七炮"},
{"id":23,"rhythm":"D5","track":"D5: PP|IP-c-PP|IP-c-PP(601|1437|1437)","lineup":"DE. 神之八炮 (same string on PE. 浮萍八炮)","scene":"DE/PE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:de_神之八炮","arch":"20260126205131"},
{"id":24,"rhythm":"ch4","track":"ch4: I-PPd|I-PPd (1738,1738)","lineup":"DE. 花园六炮","scene":"DE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:de_花园六炮","arch":"20250806093631","note":"page also has fragment 'Nd|PAd'"},
{"id":25,"rhythm":"ch4","track":"ch4: BI-c-c-PPD|BI-c-c-PPD (1738,1738)","lineup":"PE. 居中树八炮","scene":"PE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_居中树八炮","arch":"20260411151144","note":"joke line on same page: P6:PP|PP|PP|PP|N|N`(6,6,6,6,6,6) (backtick as imitater mark)"},
{"id":26,"rhythm":"ch4","track":"ch4: I-PSDd/PSD|I-PSDd/PSD (18,18)","lineup":"PE. 空炸十四炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_空炸十四炮","arch":"20241112215332"},
{"id":27,"rhythm":"ch4","track":"ch4: I-PP|I-PP (18,18)","lineup":"PE. 全金属四炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_全金属四炮","arch":"20240413134532"},
{"id":28,"rhythm":"ch4-N","track":"ch4-N：I-PP|I-PP|I-N|PP (18,18,11.5,6)","lineup":"PE. 全金属四炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_全金属四炮"},
{"id":29,"rhythm":"ch4u","track":"ch4u:IC-PP|IP-PP(1551,1924)","lineup":"PE. 心五炮","scene":"PE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_心五炮","arch":"20241008220916"},
{"id":30,"rhythm":"对ch6","track":"对ch6：Ia-PP|Pc/P|IBb-PA(N)|Pc/P（高坚果） (12,6,12,6)","lineup":"PE. 心九炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_心九炮","arch":"20241008205340"},
{"id":31,"rhythm":"ch6","track":"ch6: PP|I-PP|PP|I-PP (6,12,6,12)","lineup":"PE. 信仰八炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_信仰八炮","arch":"20260114034104"},
{"id":32,"rhythm":"P4 (cs lengths)","track":"P4: PPcccc|PPcccc|PPcccc|PPcccc (869,869,869,869)","lineup":"PE. 信仰八炮","scene":"PE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_信仰八炮"},
{"id":33,"rhythm":"ch6 (半场节奏)","track":"ch6: IPP-PPDD|PSD/BDcc|IPP-PPDD|PSD/BDcc（1150,601,1150,601）","lineup":"PE. 变奏二十二炮","scene":"PE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_变奏二十二炮","arch":"20241113001050"},
{"id":34,"rhythm":"P6-I","track":"P6-I: Pcc/PSD|Pcc/PSD|Pcc/PSD|Pcc/PSD|Pcc/PSD|PPI（6,6,6,6,6,6）","lineup":"PE. 二十二炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_二十二炮","arch":"20241008215659"},
{"id":35,"rhythm":"P6 (old)","track":"P6: P/PSD|P/PSD|P/PSD|P/PSD|A/PSD|N/PSD（6,6,6,6,6,6）","lineup":"PE. 二十二炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_二十二炮"},
{"id":36,"rhythm":"(瞬杀二十二炮阵解, unnamed)","track":"PD/PSSS|IPP-P/PD|PD/PSSS|IPP-P/PD (6,12,6,12)","lineup":"PE. 瞬杀二十二炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_二十二炮"},
{"id":37,"rhythm":"D4","track":"D4: BI-PDD/P|BI-PDD/P(18|18)","lineup":"PE. 半场十二炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_半场十二炮","arch":"20260126205041"},
{"id":38,"rhythm":"I6","track":"I6: I0P-ccP/PD(12s)","lineup":"PE. 半场十二炮 (渐强 自然出怪解)","scene":"PE","len_unit":"s (explicit 's')","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_半场十二炮"},
{"id":39,"rhythm":"P6-N","track":"P6-N: P|P|P|P|P|N (6|6|6|6|6|6)","lineup":"PE. 六线囚尸","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_六线囚尸","arch":"20240810044940"},
{"id":40,"rhythm":"P6","track":"P6：PP|PP|PP|PP|PP|PP （6，6，6，6，6，6）","lineup":"PE. 冰雨十二炮","scene":"PE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_冰雨十二炮","arch":"20240919184703"},
{"id":41,"rhythm":"P6","track":"P6：\nP|P|P|P|P|P (6,6,6,6,6,6)","lineup":"NE. 舞夜惊魂","scene":"NE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_舞夜惊魂","arch":"20250113133514","note":"name and track on separate lines"},
{"id":42,"rhythm":"T4","track":"T4:P|P|P|P","lineup":"PE. 后置八炮","scene":"PE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_后置八炮","arch":"20250711055830","note":"T = legacy name for P-series"},
{"id":43,"rhythm":"T3","track":"T3: P|P|P","lineup":"PE. 方六炮","scene":"PE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_方六炮","arch":"20250430225525"},
{"id":44,"rhythm":"ch5","track":"ch5: PPDD|IPP-B-PP|I3PP-PPdd","lineup":"PE. 守全场十五炮 (白眼关)","scene":"PE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_守全场十五炮","arch":"20240526150848","note":"same page per-wave list: 'w17: IPP-PP 1384', 'w18: I3PP-PPdd', 'w9/w19/w20: PD(炸实)/PD(精舞)'"},
{"id":45,"rhythm":"P6","track":"P6: PPN(AD)|PP-cccc|PPDD|PP-cccc|PI|PP-cccc","lineup":"PE. 守全场十五炮 (快速关)","scene":"PE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_守全场十五炮"},
{"id":46,"rhythm":"I6b","track":"I6b:I3PPDD-PP|I3PPDD-PP|I3PPDD-PP(1361×3),18=16+2+0","lineup":"PE. 神之十六炮 (键控解)","scene":"PE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_神之十六炮","arch":"20260126191700","note":"hand solutions given as lengths only: 'I5:13.5 13.5 6', 'I6n：12 12 12 6 6'"},
{"id":47,"rhythm":"I6i","track":"I6i：Ice3PD/I-cc-PPd|Ice3PD/I-cc-PPd|Ice3PD/I-cc-PPd，(1308|1308|1308)。","lineup":"PE. 残破十四炮","scene":"PE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_残破十四炮","arch":"20260127013808"},
{"id":48,"rhythm":"C2io","track":"C2io:PP|I-io","lineup":"PE. 传统四炮","scene":"PE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_传统四炮","arch":"20240526142351"},
{"id":49,"rhythm":"C6io","track":"C6io:N|PA|I-io|PA'|I-io|PP","lineup":"PE. 传统四炮","scene":"PE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_传统四炮"},
{"id":50,"rhythm":"C4io","track":"C4io:N|AA'|I|I","lineup":"PE. 机械钟无炮 / PE. 电波钟无炮 / FE. 后退无炮","scene":"PE/FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:pe_机械钟无炮","arch":"20240526145557"},
{"id":51,"rhythm":"C4-IO","track":"C4-IO:I|A|I|N","lineup":"FE. 后退无炮","scene":"FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_后退无炮"},
{"id":52,"rhythm":"双冰C2","track":"双冰C2:I-A|A'I-N(25|25)","lineup":"FE. 两仪无炮","scene":"FE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_两仪无炮","arch":"20250806075751"},
{"id":53,"rhythm":"精准C3","track":"精准C3：N'|I-N|AJa (16,18,16)","lineup":"FE. 无神无炮","scene":"FE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_无神无炮","note":"'J' letter unexplained on page (possibly 火爆辣椒 'Jalapeno'); N' = imitater doom"},
{"id":54,"rhythm":"C4-io","track":"C4-io:IN|A|A'a|N","lineup":"FE. 智能无炮","scene":"FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_智能无炮"},
{"id":55,"rhythm":"双核C3","track":"双核C3:N|IN'|AA',(17|17|17)","lineup":"FE. 智能无炮","scene":"FE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_智能无炮"},
{"id":56,"rhythm":"双核C3","track":"双核C3:A'-IN'|N|A|","lineup":"ME. 核聚变无炮","scene":"ME","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:me_核聚变无炮","arch":"20251016061921"},
{"id":57,"rhythm":"(C3, unnamed)","track":"IA|IA'|N","lineup":"FE. 心无炮","scene":"FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_心无炮","arch":"20240919183942"},
{"id":58,"rhythm":"双冰C2","track":"|I'-AA'|I-N|(25，25)","lineup":"NE. C2无神","scene":"NE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_c2无神","arch":"20250806092620"},
{"id":59,"rhythm":"C2","track":"C2：|I-B-PA'A|I-B-Nd|","lineup":"FE. 揽星 (慢速关)","scene":"FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_揽星","arch":"20250430223508"},
{"id":60,"rhythm":"C2","track":"C2：|I-B-CPA'|I-B-CPN|","lineup":"FE. 揽星 (快速关)","scene":"FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_揽星"},
{"id":61,"rhythm":"C2","track":"C2:I-B-Nd|I-B-A'/PA(25,25)","lineup":"FE. 雨夜孤灯","scene":"FE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_雨夜孤灯","arch":"20240413135353"},
{"id":62,"rhythm":"C2","track":"C2：\nI-b-BCCNA'|I-b'b-BCCP'A","lineup":"NE. 独木枝","scene":"NE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_独木枝","arch":"20250113131200","note":"contains P' and b' — meaning not defined on page"},
{"id":63,"rhythm":"C4u","track":"C4u: I'-BA-B | I-B-A'Ndd","lineup":"FE. 迟暮","scene":"FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_迟暮","arch":"20250430235928","note":"spaces around |"},
{"id":64,"rhythm":"C4u","track":"C4u: |I'-BA-B|I-B-A'Ndd|","lineup":"FE. 鬼位四炮","scene":"FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_鬼位四炮","arch":"20250620183340"},
{"id":65,"rhythm":"C4u","track":"C4u: |I'-BB|I-NDAd|","lineup":"FE. 鬼位四炮","scene":"FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_鬼位四炮"},
{"id":66,"rhythm":"c5u (lowercase)","track":"c5u: I+Pd|I+Pd|A'+Nd (2030,2030,1250)","lineup":"FE. 帆船四炮 (出怪类型1)","scene":"FE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_帆船四炮","arch":"20240413124254","note":"'+' used instead of '-'; variant 'c5u: I+PD|I+PD|A'+Nd (2030,2030,1250)'"},
{"id":67,"rhythm":"邻c6u","track":"邻c6u：I+PP|I+PP|N|PP (2030,2030,750,750)","lineup":"FE. 帆船四炮","scene":"FE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_帆船四炮","note":"variants: '邻c6u: I+PP|I+PP|N|cPP (1750,1750,750,1000)', '邻c6u: I+PD|I+PD|NA|cPD (1750,1750,750,1000)'"},
{"id":68,"rhythm":"c4u","track":"c4u: I-Nd|I-PSD/A (25,25)","lineup":"FE. 落月四炮 (PSD键控解)","scene":"FE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_落月四炮","arch":"20241008212750"},
{"id":69,"rhythm":"C4u","track":"C4u: IP-Nd|IPD-A/P (25,25)","lineup":"FE. 落月四炮 (手动旧解)","scene":"FE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_落月四炮"},
{"id":70,"rhythm":"C4u","track":"C4u: IPA-Nd|IPD-P (25,25)","lineup":"FE. 落月四炮 (手动新解)","scene":"FE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_落月四炮"},
{"id":71,"rhythm":"C5u","track":"C5u: NA|IP-Pd|I-Pd (7.5,25-,20+)","lineup":"FE. 落月四炮","scene":"FE","len_unit":"s (with -/+ qualifiers)","src":"https://wiki.pvz1.com/doku.php?id=阵型:fe_落月四炮"},
{"id":72,"rhythm":"邻C6u","track":"邻C6u:PP|I+PP|I+PP|N","lineup":"FE. 底线四炮 (原作者)","scene":"FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:多场地_底线四炮","arch":"20260411145033"},
{"id":73,"rhythm":"邻C7u","track":"邻C7u:PP|I+PP|I+PP|AAA|N","lineup":"FE. 底线四炮","scene":"FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:多场地_底线四炮"},
{"id":74,"rhythm":"对C6u","track":"对C6u：I-PP|PA|I-PaA'|N(19,6,19,6)","lineup":"FE. 底线四炮","scene":"FE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:多场地_底线四炮"},
{"id":75,"rhythm":"(old free-form, unnamed)","track":"Pa|IPa+Pa|N|Pa|IAa+Pa|Pa|IPa+iPa|N","lineup":"PE. 底线四炮 (原作者轨道, '地狱绘图')","scene":"PE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:多场地_底线四炮","note":"lowercase i"},
{"id":76,"rhythm":"邻C6u","track":"邻C6u I-PP|I-PP|N|PP 1976|1976|750|749","lineup":"[FE] 经典四炮 (AvZ DSL example)","scene":"FE","len_unit":"cs (bare tail lengths)","src":"AsmVsZombies repo: tutorial/dsl/4 脚本示例：[FE] 经典四炮.md (line 5: '// 节奏：邻C6u I-PP|I-PP|N|PP 1976|1976|750|749')"},
{"id":77,"rhythm":"邻C7u","track":"邻C7u: IP-PP|IP-PP|PA|N|PP (17,17,6,7.5,6)","lineup":"ME. 神之六炮","scene":"ME","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:me_神之六炮","arch":"20240526141628"},
{"id":78,"rhythm":"(2011 first-post track, ~S5-P5逐波)","track":"PP|PP|N(a)|PP|IA'-PP|PP|A₁Aa~PP|N|PP|PP-A'-PP-A-PP","lineup":"ME. 神之六炮 (首发帖, 随尘归海)","scene":"ME","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:me_神之六炮","note":"page: A'=复制樱桃, A₁=辣椒, ~=可能意外刷新可能不刷"},
{"id":79,"rhythm":"C7u","track":"C7u: I-PP|I'-PP|PA|N|PP (18,18,6,6,6)","lineup":"NE. 压碑五炮","scene":"NE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_压碑五炮","arch":"20240810042433"},
{"id":80,"rhythm":"C7u","track":"C7u:I-PP|I-PP|PA|N|PP(18,18,6,6,6)","lineup":"NE. 智商五炮","scene":"NE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_智商五炮","arch":"20250521114849"},
{"id":81,"rhythm":"C8u","track":"C8u: IP-PP|IP-PP|PP|PA|N|PP (13,13,6,6,6,6)","lineup":"NE. 生死劫","scene":"NE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_生死劫","arch":"20260411135727"},
{"id":82,"rhythm":"C7u*","track":"C7u*: IP-PP|IP-N|PP|PA|PP","lineup":"NE. 生死劫","scene":"NE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_生死劫"},
{"id":83,"rhythm":"邻C8u","track":"邻C8u: Ic-PP|IA'-PP|PAc|PPc|Na|PPc(13,13,6,6,6,6)","lineup":"NE. 雪花七炮","scene":"NE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_雪花七炮","arch":"20240413121939"},
{"id":84,"rhythm":"间C7u","track":"间C7u: IPP-Bcc-PPDD|APD/Pdcc|IPP-Bcc-PPDD|A'PD/Pd|NDd/Pdcc (17,6,17,6,6)","lineup":"NE. 二十炮","scene":"NE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_二十炮","arch":"20240810035421"},
{"id":85,"rhythm":"间C8u","track":"间C8u: IP-Pd/PD|PSD/Pdcc|IP-Pd/PD|PSDa/PdC|ND/cPAN|PDD/PDcc (14,6,14,6,6,6)","lineup":"NE. 二十炮","scene":"NE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_二十炮","note":"both c and C in same string"},
{"id":86,"rhythm":"对 N6","track":"对 N6: PPdd|I-PPDD|Ndd|I-PPDD(746|1755|746|1755)","lineup":"NE. 中开十二炮 (same on NE. 矩形十二炮 with space before '(')","scene":"NE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_中开十二炮","arch":"20260126201718"},
{"id":87,"rhythm":"对 N6","track":"对 N6: PPdd|I0P-B-PPDd|Ndd|I0P-B-PPDd(746|1755|746|1755)","lineup":"NE. 丑十四炮","scene":"NE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=阵型:ne_丑十四炮","arch":"20260126225653"},
{"id":88,"rhythm":"per-wave (逐波) list","track":"w1:c(二路)PSD/PDD\nw2:I'(二路)A'C|B-PD(连拦)/PD\nw3:PDD/PDcD\nw4:I-A|B-PD/PD\nw5:PSN/PSN\nw6:PSD/PD\nw7:PDD/PDD\nw8:I'A'C|B-PD/PD\nw9:I-PP|PDD/PDD\nw19:PSD/PSD","lineup":"ME. 二十炮 (ek-z/吸水泵 手控解)","scene":"ME","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=阵型:me_二十炮","arch":"20250219012419"},
{"id":89,"rhythm":"P5 (tutorial, spaced)","track":"P5: PP | PP | PP | PP | PP (7, 7, 7, 7, 7)","lineup":"RE. 天台十炮","scene":"RE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系","arch":"20240413115207","note":"tutorial also writes P4 as 'PP | PP | PP | PP' + '(9, 9, 9, 9)（单位：秒）'"},
{"id":90,"rhythm":"樱代P6 (tutorial)","track":"PP | PP | PP | PP | PP | PA（尽可能晚用樱桃）| PP | PP | PP | PP","lineup":"RE. 十一炮","scene":"RE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系"},
{"id":91,"rhythm":"(灰烬代奏 P4, half-field '/')","track":"PP | PP | PP | PP | a+N'/A | A'/N | PP | PP | PP+收尾\nPP | PP | PP | PP | A'/a+N' | N/A | PP | PP | PP | PP+收尾","lineup":"FE. 真水无八炮","scene":"FE","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系","note":"page text: '用了 / 符号分隔上下半场'; 'N'表示模仿者核武'"},
{"id":92,"rhythm":"C3 (无炮, tutorial)","track":"C3: A'I | AN | N'","lineup":"(无炮 fixed-card-order example; lineup not named in extracted text)","scene":"?","len_unit":"none","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系"},
{"id":93,"rhythm":"精准C2","track":"精准C2：IN|IAA',(25|25)","lineup":"(table example)","scene":"night/无炮","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系 (常用节奏一览)"},
{"id":94,"rhythm":"双核C2","track":"双核C2：IA'-N|A-N,(25|25)","lineup":"(table)","scene":"无炮","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系"},
{"id":95,"rhythm":"精准C3","track":"精准C3：IN|N|AA'a,(18|16|16)","lineup":"FE无神无炮（杨桃版）","scene":"FE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":96,"rhythm":"邻双冰C3 (wiki-mirror only)","track":"邻双冰C3：IA'|Ia-A|N,(25|20|6)","lineup":"(table)","scene":"无炮","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=攻略:节奏命名体系修正方案","arch":"20240526150632"},
{"id":97,"rhythm":"间双核C4","track":"间双核C4：I-N|A|A'a|N,(25|9|9|9|)","lineup":"(table)","scene":"无炮","len_unit":"s (trailing empty slot)","src":"https://wiki.pvz1.com/doku.php?id=攻略:节奏命名体系修正方案"},
{"id":98,"rhythm":"邻双核C4","track":"邻双核C4：a-io|IA'-A|N|N,(20+|15+|6|9)","lineup":"(table)","scene":"无炮","len_unit":"s (+ qualifiers)","src":"https://wiki.pvz1.com/doku.php?id=攻略:节奏命名体系修正方案"},
{"id":99,"rhythm":"邻双冰C4","track":"邻双冰C4：Ia|IA'-io|A|N,(20+|15+|9|6)","lineup":"(table)","scene":"无炮","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=攻略:节奏命名体系修正方案"},
{"id":100,"rhythm":"间双冰C4","track":"间双冰C4：N|aI-io|A|A'I-io,(9|16+|9|16+)","lineup":"(table)","scene":"无炮","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=攻略:节奏命名体系修正方案"},
{"id":101,"rhythm":"C4-io","track":"C4-io: IN|A|A'a|N,","lineup":"FE智能无炮","scene":"FE","len_unit":"none (trailing comma)","src":"https://www.bilibili.com/read/cv13534815"},
{"id":102,"rhythm":"P2-2I","track":"P2-2I: PI|PI, (18|18)","lineup":"DE后置四炮","scene":"DE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":103,"rhythm":"P3","track":"P3: P|P|P, (12|12|12)","lineup":"PE方六炮","scene":"PE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":104,"rhythm":"P4","track":"P4: P|P|P|P, (9|9|9|9)","lineup":"PE经典八炮","scene":"PE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":105,"rhythm":"P5","track":"P5: P|P|P|P|P, (7|7|7|7|7)","lineup":"ME十炮","scene":"ME","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":106,"rhythm":"P5u (wiki table)","track":"P5u：P|P|P|P|P,(6.6|7.5|6.6|7.5|6.6)","lineup":"(table)","scene":"-","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系"},
{"id":107,"rhythm":"P6","track":"P6: P|P|P|P|P|P, (6|6|6|6|6|6)","lineup":"PE经典12炮","scene":"PE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":108,"rhythm":"P6-N","track":"P6-N: P|P|P|P|P|N (6|6|6|6|6|6)","lineup":"PE六线囚尸","scene":"PE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":109,"rhythm":"P6-2I","track":"P6-2I：PD|PD|PI|PD|PD|PI,(6|6|6|6|6|6)","lineup":"(table)","scene":"-","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系"},
{"id":110,"rhythm":"P6-2N","track":"P6-2N：P|P|P|P|N|N,(6|6|6|6|6|6)","lineup":"(table)","scene":"-","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系"},
{"id":111,"rhythm":"S3","track":"S3: I-P|P, (21|15)","lineup":"NE后置四炮","scene":"NE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table instead: 'S3：I-P|P,(25|10)'"},
{"id":112,"rhythm":"S3-io","track":"S3-io: I-|PSSN(AA'), (25|10)","lineup":"NE瞬杀三炮","scene":"NE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"'I-' wave with nothing after the dash; parenthesised ops inside a wave"},
{"id":113,"rhythm":"S4","track":"S4：I-P|P|P,(18|9|9)","lineup":"(table)","scene":"-","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系"},
{"id":114,"rhythm":"S5","track":"S5: IP-PPD|PPD|PPD|PPD, (17|6|6|6)","lineup":"ME13炮","scene":"ME","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table: 'S5：I-P|P|P|P,(18|6|6|6)'"},
{"id":115,"rhythm":"S6","track":"S6: I-P|P|P|P|P, (12|6|6|6|6)","lineup":"PE地刺12炮","scene":"PE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"wiki mirror writes 'I-P|P|PD|P|P, (12|6|6|6|6)'"},
{"id":116,"rhythm":"ch4","track":"ch4: I-P|I-P, (17.5|17.5)","lineup":"DE花园六炮","scene":"DE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table: 'ch4: I-P|I-P, (18|18)'"},
{"id":117,"rhythm":"ch4u","track":"ch4u: I-Pd|IP-PD, (16.2|18.6)","lineup":"PE小②炮","scene":"PE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table: 'ch4u: I-P|I-P, (16|19)'"},
{"id":118,"rhythm":"ch4*","track":"ch4*: I3PD-P|I3PD-P, (19|19)","lineup":"RE中开12炮","scene":"RE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table: 'ch4*: I-P|I-P, (20|20)'"},
{"id":119,"rhythm":"ch5","track":"ch5: IP-PP|IP-PP|PP, (14.4|14.4|6)","lineup":"DE神之八炮","scene":"DE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table: 'ch5: I-P|I-P|P, (15|15|6)'"},
{"id":120,"rhythm":"ch5u","track":"ch5u: IP-PP|I-PP|P, (17|13|6)","lineup":"RE神之七炮","scene":"RE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":121,"rhythm":"ch6 (对ch6)","track":"ch6: I-P|P|I-P|P, (11.5|6|11.5|6)","lineup":"PE饕餮之宴","scene":"PE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table: '对ch6: I-P|P|I-P|P, (12|6|12|6)'"},
{"id":122,"rhythm":"邻ch6","track":"邻ch6: IP-PD|IP-PD|PSD|PSD, (11.5|11.5|6|6)","lineup":"手控PE垫材24炮","scene":"PE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table: '邻ch6: I-P|I-P|P|P, (12|12|6|6)'"},
{"id":123,"rhythm":"C6i","track":"C6i: I-PD|I-PD|I-PD, (12|12|12)","lineup":"PE太极14炮","scene":"PE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":124,"rhythm":"C6x (原C6iu-54s)","track":"C6x: I-P|IAA'-P|IN-P, (16|19|19)","lineup":"PE中空四炮","scene":"PE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":125,"rhythm":"C6i*","track":"C6i*: I-P|I-P|I-P, (15|15|15)","lineup":"PE望云轩六炮","scene":"PE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":126,"rhythm":"ch4-N (was C7i)","track":"I-P|I-P|I-N|P, (18|18|12|6)","lineup":"(reference track)","scene":"day","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":127,"rhythm":"C6i-N (was C8i)","track":"I-P|I-P|I-P|N|P, (12|12|12|6|6)","lineup":"(reference track)","scene":"day","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":128,"rhythm":"C4u","track":"C4u: IPPB-A'PA|IPPB-Nd, (25|25)","lineup":"FE三角板六炮","scene":"FE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table: 'C4u: I-P|I-P, (25|25)'"},
{"id":129,"rhythm":"C5u","track":"C5u: BI-PPDA|BI-PPD|A'B-NDd, (20.3|20.3|9.4)","lineup":"FE河洛十炮","scene":"FE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table: 'C5u: I-P|I-P|N, (21|21|8)'"},
{"id":130,"rhythm":"邻C6u","track":"邻C6u: I-P|I-P|N|P, (1975|1975|750|750)","lineup":"FE最简四炮","scene":"FE","len_unit":"cs (only night row in cs)","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table: '邻C6u: I-P|I-P|N|P, (18|18|9|9)'"},
{"id":131,"rhythm":"对C6u","track":"对C6u: I-PP|PA|I-PA'a|N, (19|6|19|6)","lineup":"FE底线四炮","scene":"FE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":132,"rhythm":"邻C7u","track":"邻C7u: I-PP|I-PP|PA|N|PP, (18|18|6|6|6)","lineup":"NE压碑五炮","scene":"NE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":133,"rhythm":"间C7u","track":"间C7u: I-PP|PP|I-PP|N|PP, (17|6|17|6|6)","lineup":"NE无保护六炮","scene":"NE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":134,"rhythm":"邻C8u","track":"邻C8u: I-PP|IA'-PP|N|PP|PA|PP, (13|13|6|6|6|6)","lineup":"NE雪花七炮","scene":"NE","len_unit":"s","src":"https://www.bilibili.com/read/cv13534815"},
{"id":135,"rhythm":"间C8u","track":"间C8u: I-P|P|I-P|P|N|P, (13|6|13|6|6|6)","lineup":"(table; named lineup NE20炮)","scene":"NE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系"},
{"id":136,"rhythm":"对C8u","track":"对C8u: I-P|P|P|I-P|N|P, (13|6|6|13|6|6)","lineup":"(table; named lineup FE9列24炮)","scene":"FE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系"},
{"id":137,"rhythm":"C9u (ch9)","track":"C9u: I-P|P|I-P|P|N|P|P, (12|6|12|6|6|6|6)","lineup":"(table; named lineup 手控FE24炮)","scene":"FE","len_unit":"s","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系"},
{"id":138,"rhythm":"I4i","track":"I4i: I-Pd|I-Pd, (1560连续冰波)","lineup":"DE水晶杯10炮","scene":"DE","len_unit":"cs","src":"https://www.bilibili.com/read/cv13534815","note":"wiki table: 'I4i：I3PD-P|I3PD-P,(1560连续冰波)'"},
{"id":139,"rhythm":"I6i","track":"I6i: I3PD-P|I3PD-P|I3PD-P, (1362连续冰波)","lineup":"PE16炮守全场","scene":"PE","len_unit":"cs","src":"https://www.bilibili.com/read/cv13534815"},
{"id":140,"rhythm":"I5","track":"I5：IP-PD|IP-PD|PD,(1330|1330|601)","lineup":"PE胆守 (钻石15炮)","scene":"PE","len_unit":"cs","src":"https://wiki.pvz1.com/doku.php?id=技术:节奏体系"}
]
```

## Rhythm-name grammar (from 修正方案, verbatim-level)
- Six classes: `Cx` (无炮, param = 过怪量), `Px`, `Sx`, `chX` (day double-ice; special `C6i`), `Cxu` (night double-ice; `u` is part of the class name), `Ix` (day I-series, `i` suffix = 连续冰波). Param for cannon lineups: 加速波=1, 减速波=2.
- Generic suffixes: `-io`, `-xxxx` (cycle length in cs, e.g. historical `C6iu-54s`, `邻C6u-54s`, `Cxu-36s`). P: `u`, `-xI`, `-xN`. S: `-xN`. ch: prefixes 对/邻 (ch6 only), `u`, `*`, `-xN`. Cxu: prefixes 邻/间/对, `*`. Ix: `i`.
- Legacy names seen in lineup pages: `T3`/`T4` (=P3/P4), `D4`/`D5`/`对D6`/`邻D6` (D-series = ? unexplained; used for 'ch'-like day tracks), `N6`/`对 N6` (night, unexplained), `I6b`/`I6n`, `C2io`/`C6io`/`C4io`/`C4-IO`.
- Lengths list separator inside parentheses is `,` or `|` (both common), fullwidth `，` also appears; parentheses may be fullwidth `（）`; a comma may precede the parenthesis (`, (18|18)`).

## Local file paths (scratchpad)
- Raw/converted wiki snapshots: `C:\Users\mike2\AppData\Local\Temp\claude\C--MyAILab\9b6c7fa6-bf46-4013-a521-0bd4ad13cfc3\scratchpad\wk\*.html|*.txt`
- 技术:节奏体系 text: `...\scratchpad\jz.txt`; cv13534815 text: `...\scratchpad\cv.txt` (JSON `cv.json`)
- pe_经典八炮 snapshot: `...\scratchpad\pe8.html`

## Unknowns
1. forum.pvz1.com 轨道读本 v3.1 could not be fetched (host 124.156.143.94 refused/timed out; no Wayback capture found). Its definitions of simplified forms (e.g. 'PIPIPN', 'P|I-P|I-P|N'), shovel-replant P', ICE3 'I3', 'c' vs 'C', and cs-vs-s convention were NOT verified from that document; the strings 'PIPIPN', 'IP-P|IP-P 1738|1738' and '(6, 21, 9)' mentioned in the task were NOT found in any source reached. Closest real examples: 'ch4: I-PPd|I-PPd (1738,1738)' (DE花园六炮), 'ch4: BI-c-c-PPD|BI-c-c-PPD (1738,1738)' (PE居中树八炮), AvZ '邻C6u I-PP|I-PP|N|PP 1976|1976|750|749' (bare tail lengths), 'C2：|I-B-PA'A|I-B-Nd|' (no lengths).
2. Zhihu articles p/45182429 (Be2SiO4), p/379738079 and p/379924798 (白菜 轨道理论) returned 403 and have no Wayback capture; not included.
3. pvz.tools /lineup/ data (lineup.yml / lineup_string.json, 339 lineups) contains only placement strings, no 轨道 strings.
4. Meaning of letters P' (ne_独木枝), b/b', J (fe_无神无炮 'AJa'), I0 (I0P), D-series names (D4/D5/对D6) and N6 are not defined on the pages fetched; 'c' (lowercase) appears to mean a cob used as filler/non-activation shot (PPcccc, Pcc) and 'C' an uppercase variant, but no page reached states this explicitly. 'd' = 尾炸 (delayed/tail D) is stated on 攻略:复合运算 ('PPdd（尾炸）').
5. All wiki text was obtained from Wayback snapshots dated 2024-04 to 2026-04 (timestamps given per row where known; rows with arch omitted came from the same snapshot set). Live pages may have been edited since. HTML->text conversion preserved characters but line breaks inside a few tracks (ids 41, 62) were introduced by markup; in the source they are rendered as heading + code block.
6. Some lineup pages in the CDX list failed to download even on retry (ne_双核拦截少炮阵集, ne_快六炮, ne_最少花无炮, pe_大帝八炮, pe_守全场十六炮, pe_简三炮, pe_经典八炮 (2024 snapshot; 2025 snapshot was used instead)); 九章阵华录 snapshot was binary garbage.

## Sources
- 节奏命名体系修正方案（2021/10/11） (bilibili cv13534815, via article API JSON in scratchpad): https://www.bilibili.com/read/cv13534815
- 技术:节奏体系 - PVZ Wiki (Wayback 20240413115207): https://wiki.pvz1.com/doku.php?id=技术:节奏体系
- 攻略:节奏命名体系修正方案 - PVZ Wiki (Wayback 20240526150632): https://wiki.pvz1.com/doku.php?id=攻略:节奏命名体系修正方案
- 攻略:复合运算 - PVZ Wiki (Wayback 20240612175659): https://wiki.pvz1.com/doku.php?id=攻略:复合运算
- 阵型:pe_经典八炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_经典八炮
- 阵型:fe_经典八炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:fe_经典八炮
- 阵型:pe_二十四炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_二十四炮
- 阵型:pe_二十二炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_二十二炮
- 阵型:pe_无冰瓜二十炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_无冰瓜二十炮
- 阵型:pe_新二十炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_新二十炮
- 阵型:pe_信仰八炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_信仰八炮
- 阵型:pe_全金属四炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_全金属四炮
- 阵型:pe_半场十二炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_半场十二炮
- 阵型:pe_变奏二十二炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_变奏二十二炮
- 阵型:pe_守全场十五炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_守全场十五炮
- 阵型:pe_神之十六炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_神之十六炮
- 阵型:pe_残破十四炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_残破十四炮
- 阵型:pe_心九炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_心九炮
- 阵型:pe_心五炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_心五炮
- 阵型:pe_居中树八炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_居中树八炮
- 阵型:pe_传统四炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:pe_传统四炮
- 阵型:fe_落月四炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:fe_落月四炮
- 阵型:fe_帆船四炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:fe_帆船四炮
- 阵型:多场地_底线四炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:多场地_底线四炮
- 阵型:ne_二十炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:ne_二十炮
- 阵型:ne_雪花七炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:ne_雪花七炮
- 阵型:ne_丑十四炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:ne_丑十四炮
- 阵型:me_二十炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:me_二十炮
- 阵型:me_神之六炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:me_神之六炮
- 阵型:fe_智能无炮 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:fe_智能无炮
- 阵型:ne_独木枝 - PVZ Wiki: https://wiki.pvz1.com/doku.php?id=阵型:ne_独木枝
- AsmVsZombies tutorial DSL example [FE] 经典四炮: https://github.com/vector-wlc/AsmVsZombies/blob/master/tutorial/dsl/4%20%E8%84%9A%E6%9C%AC%E7%A4%BA%E4%BE%8B%EF%BC%9A%5BFE%5D%20%E7%BB%8F%E5%85%B8%E5%9B%9B%E7%82%AE.md
- 轨道读本v3.1 – PVZ Forum (unreachable, listed for completeness): https://forum.pvz1.com/en/se-general/%E8%BD%A8%E9%81%93%E8%AF%BB%E6%9C%ACv3-1/
- Wayback CDX index used to locate wiki snapshots: https://web.archive.org/cdx/search/cdx?url=wiki.pvz1.com/doku.php*&output=txt