# Research: lineup-code (2026-08-22, agent-gathered; verify before relying on)

## Source of truth
pvztoolkit (GPL, lmintlcx) `src/lineup.cpp`, `inc/lineup.h`, `src/utils.cpp` (base64). Raw: https://raw.githubusercontent.com/lmintlcx/pvztoolkit/master/src/lineup.cpp etc. Local copies downloaded to `C:\Users\mike2\AppData\Local\Temp\claude\C--MyAILab\9b6c7fa6-bf46-4013-a521-0bd4ad13cfc3\scratchpad\{lineup.cpp,lineup.h,utils.cpp}`. The data files `lineup.yml` and `lineup_string.json` are also in that scratchpad dir. The AvZ repo copy (`scratchpad/AsmVsZombies`, commit c42676c, 2026-06-26) contains NO lineup-code parser (no `ASetPlants`, no base64, no "lineup"); AvZ users paste codes into pvztoolkit / pvz.tools, so the format below is the only one.

## Lineup CODE (阵型代码) — binary layout → base64

Decode algorithm (exact, verified by re-implementing and round-tripping all 339 published lineups):

1. **Base64-decode** the string. Alphabet: standard `A-Z a-z 0-9 + /`, `=` padding. Decoder also accepts URL-safe `-`(→62) and `_`(→63) (decode table maps both 43 '+' and 45 '-' to 62, and 47 '/' and 95 '_' to 63). Encoder always emits `+ /`. Input validation: regex `[A-Za-z0-9+/=]{18,164}` or `[A-Za-z0-9\-_=]{18,164}`, `len % 4 == 0`, at most two `=`. Decoded buffer max 128 bytes.
2. **XOR every byte with 0x54** (whole decoded buffer, including trailer byte).
3. **Trailer byte** = last byte: `rake_row = b >> 4`, `scene = b & 0x0F`.
   - scene must be < 6: 0=DE day, 1=NE night, 2=PE pool, 3=FE fog, 4=RE roof, 5=ME moon (in-game scene ids).
   - rake_row: 0 = no rake; else 1..5 (5-row scenes) or 1..6 (pool/fog); rows 3,4 forbidden in pool/fog (water).
4. **zlib-inflate** bytes `[0 .. size-2]` (everything before the trailer; it is a normal zlib stream with 2-byte header `78 DA`, deflate level 9, adler32 trailer — inflating gives the CRC check for free; there is no other checksum). Output must be exactly `rows*9*2` bytes where rows = 6 for scene 2/3, else 5. (Encoder compresses only the first rows*9 uint16s of a 54-cell array.)
5. Output = array of **uint16 little-endian** cells, row-major, index `r*9 + c` (r,c zero-based, 9 columns). Bit layout per cell (MSB→LSB):

| bits | mask | field | meaning |
|---|---|---|---|
| 15..10 | 0xFC00 | plant | 0 = none, else **seed_type + 1** (so 48 = Cob Cannon 47, 45 = Winter Melon 44, 43 = Gloom 42, 42 = Twin Sunflower 41, 38 = Umbrella 37, 15 = Ice-shroom 14, ...) |
| 9 | 0x0200 | plant_im | main plant is Imitater |
| 8 | 0x0100 | plant_awake | 1 = awake; 0 only for sleep-capable mushrooms in day scenes (0,2,4) |
| 7..6 | 0x00C0 | base | 0 none, 1 Lily Pad(16), 2 Flower Pot(33), 3 Gravestone(0x32) |
| 5 | 0x0020 | base_im | lily/pot is Imitater |
| 4 | 0x0010 | pumpkin | Pumpkin(30) present |
| 3 | 0x0008 | pumpkin_im | pumpkin is Imitater |
| 2 | 0x0004 | coffee | Coffee Bean(35) present |
| 1 | 0x0002 | coffee_im | coffee is Imitater |
| 0 | 0x0001 | ladder | Ladder (0x30) on cell |

Encode = reverse: pack cells, zlib `compress2(..., Z_BEST_COMPRESSION)` of the first rows*9*2 bytes, append trailer `(rake_row<<4)|scene`, XOR all with 0x54, base64. NOTE: codes are **not canonical** — different deflate implementations produce different bytes for the same grid (my zlib-ng re-encode matched 318/339 published strings byte-for-byte; the other 21 decode to identical payloads). Unit tests must compare decoded grids, not code strings (or pin a specific zlib).

Sleep rule used when converting string→data: `awake = !((scene in {0,2,4}) && may_sleep[type] && state_field == 0)`. may_sleep types (index = seed type): 8,9,10,12,13,14,15,24,31,42 (Puff, Sun-shroom, Fume, Hypno, Scaredy, Ice, Doom, Sea-shroom, Magnet-shroom, Gloom).

Sorting weight (informational): scene*1e16 + counts of plant==48 (cob)*1e14, 43 (gloom)*1e12, 42 (twin sun)*1e10, 45 (winter melon)*1e8, pumpkin*1e6, lily*1e4, pot*1e2, ladder*1. (Source comments mislabel these as 春哥/曾哥/双子/冰瓜 because of the +1 offset.)

## Lineup STRING (阵型字符串, older text format, used in lineup_string.json)
Grammar (from source regex): `SCENE(,ITEM)*` where
`SCENE ∈ 0..5` : **0=PE pool, 1=FE fog, 2=DE day, 3=NE night, 4=RE roof, 5=ME moon** (different numbering from the binary scene nibble!) and
`ITEM = TYPEHEX ROW COL STATE F4 IM [extra...]` space-separated:
- TYPEHEX: 1–2 hex digits, seed type 0..0x32 (no leading zeros, e.g. `E`=14 Ice-shroom, `1E`=30 Pumpkin, `10`=16 Lily, `21`=33 Pot, `23`=35 Coffee, `2F`=47 Cob, `30`=48 Ladder, `31`=49 Rake, `32`=50 Gravestone)
- ROW 1–6, COL 1–9 (1-based)
- STATE 0–2: for mushrooms 0=asleep/1=awake (gloom in day scenes written as `2A r c 1 0 0`); for gravestone 0–2 appearance index; otherwise 0
- F4 0–4: Sun-shroom grow stage (`9 r c 1 2 x` = big sun-shroom), gravestone variant 1–4; else 0
- IM: `1` = Imitater, else `0` (parser checks field index 5 == "1")
- Items are ordered per cell: base (lily/pot) first, then main plant, then pumpkin, then ladder. Rake item: `31 ROW 1 0 0 0` (only row used). Types > 0x32 ignored.
Multiple items may share a cell (e.g. `10 3 1 0 0 0,2F 3 1 0 0 0` = lily + cob).

## Data files
- https://pvz.lmintlcx.com/files/lineup.yml (linked from https://pvz.tools/lineup/ ; `https://pvz.tools/files/lineup.yml` 404s). Format: first line `#! pvztoolkit`, comment `# 版本: 2020.12.04 (数量: 339)`, then lines `"[DE] 双核无炮": LI5HDH3kARQ...` (YAML scalar map: name → code). Counts: DE 45, NE 49, PE 143, FE 54, RE 28, ME 20.
- https://pvz.lmintlcx.com/files/lineup_string.json : `{"version":20201204,"author":"lmintlcx","counts":339,"lineup":[{"name":"[DE] 双核无炮","string":"2,29 1 1 0 0 0,1E 1 1 0 0 0,..."}, ...]}`.
- The pvz.tools site itself has no JS decoder (only jQuery/bootstrap/site script); "在线布阵" links to external http://jspvz.com/ArrayDesign/ and http://lonelystar.org/ArrayDesign/ (not fetched).

## Test vectors (decoded with the algorithm above; plant numbers given as in-game seed type, i.e. field-1)

**A. `LI43NMQIfe3BVVEc+sHIXUAPX7dvSCBUt1TS1OE8dn9XgraGkdQ1ZlSKiEUaVw==`** (AvZ tutorial "[FE] 经典四炮", not in the 339 list)
- 46 bytes after base64; after XOR 0x54: `78da6360905c29b995010548ae959c09145b0be33b1c7400e3008680b568222b03d6e2d2c580613200dedc114e03` → trailer `0x03` = scene 3 (FE), rake 0. Inflated 108 bytes (6 rows):
  `0000 19a9 19b5 0000 0000 0000 0000 0000 0000 | 0000 19ad 1999 19b5 00ad 0000 0000 0000 0000 | 40c1 4000 40c1 4000 5000 50ad 0000 0000 0000 | 40c1 4000 40c1 4000 50a9 50ad 0000 0000 0000 | 0000 19ad 1999 19b5 00ad 0000 0000 0000 0000 | 0000 19a9 19b5 0000 0000 0000 0000 0000 0000` (hex dump is big-endian-looking per cell; actual bytes are LE, e.g. cell(1,2) bytes `a9 19` = 0xA919? NO — raw bytes are `19 a9`→ no: value 0xA919). To be exact: cell(1,2)=0xA919 = plant 42→TwinSunflower(41), awake, pumpkin+im, ladder. cell(1,3)=0xB519 = WinterMelon(44), awake, pumpkin+im, ladder. Row2: c2=0xAD19 Gloom(42), c3=0x9919 Umbrella(37), c4=0xB519 WinterMelon, c5=0xAD00 Gloom (no pumpkin). Row3: c1=0xC140 Cob(47)+lily, c2=0x0040 lily, c3=0xC140 Cob+lily, c4=0x0040 lily, c5=0x0050 lily+pumpkin(non-im), c6=0xAD50 Gloom+lily+pumpkin. Row4: c1 Cob+lily, c2 lily, c3 Cob+lily, c4 lily, c5=0xA950 TwinSun+lily+pumpkin, c6=0xAD50 Gloom+lily+pumpkin. Row5 = row2. Row6 = row1.
- Note the cell values as 16-bit integers: 0xA919, 0xB519, 0xAD19, 0x9919, 0xAD00, 0xC140, 0x0040, 0x0050, 0xAD50, 0xA950.

**B. `LI43NMQIfe3BldJVXRz6wchdQA9ft29IIFS3VM/U4Tx2f1eCtoaR1DVmVFO7RlZW`** = "[PE] 经典四炮" (lineup.yml line 128). Trailer 0x02 → scene 2 PE, rake 0. Same grid as A plus Ice-shrooms asleep: cell(1,4)=0x3C00 (plant field 15 → Ice-shroom 14, awake=0), cell(3,5)=0x3C50 (Ice-shroom asleep + lily + pumpkin), cell(6,4)=0x3C00. Corresponding string (from lineup_string.json): `0,29 1 2 0 0 0,1E 1 2 0 0 1,30 1 2 0 0 0,2C 1 3 0 0 0,1E 1 3 0 0 1,30 1 3 0 0 0,E 1 4 0 0 0,2A 2 2 1 0 0,1E 2 2 0 0 1,30 2 2 0 0 0,25 2 3 0 0 0,1E 2 3 0 0 1,30 2 3 0 0 0,2C 2 4 0 0 0,1E 2 4 0 0 1,30 2 4 0 0 0,2A 2 5 1 0 0,10 3 1 0 0 0,2F 3 1 0 0 0,10 3 2 0 0 0,10 3 3 0 0 0,2F 3 3 0 0 0,10 3 4 0 0 0,10 3 5 0 0 0,E 3 5 0 0 0,1E 3 5 0 0 0,10 3 6 0 0 0,2A 3 6 1 0 0,1E 3 6 0 0 0,10 4 1 0 0 0,2F 4 1 0 0 0,10 4 2 0 0 0,10 4 3 0 0 0,2F 4 3 0 0 0,10 4 4 0 0 0,10 4 5 0 0 0,29 4 5 0 0 0,1E 4 5 0 0 0,10 4 6 0 0 0,2A 4 6 1 0 0,1E 4 6 0 0 0,2A 5 2 1 0 0,1E 5 2 0 0 1,30 5 2 0 0 0,25 5 3 0 0 0,1E 5 3 0 0 1,30 5 3 0 0 0,2C 5 4 0 0 0,1E 5 4 0 0 1,30 5 4 0 0 0,2A 5 5 1 0 0,29 6 2 0 0 0,1E 6 2 0 0 1,30 6 2 0 0 0,2C 6 3 0 0 0,1E 6 3 0 0 1,30 6 3 0 0 0,E 6 4 0 0 0`

**C. `LI5HzH3tAibxsALS11gEdBRxRVTCikaVVA==`** = "[DE] 柱子五炮". 25 bytes; XORed: `78da139829b95672a5e45686830c50204025110096de12c100`; trailer 0x00 → scene 0 DE, rake 0; inflates to 90 bytes (5 rows), every row identical: c1=0x9910 Umbrella(37)+pumpkin(non-im), c2=0xAD19 Gloom+pumpkin(im)+ladder, c3=0xA919 TwinSun+pumpkin(im)+ladder, c4=0xB519 WinterMelon+pumpkin(im)+ladder, c5=0xC100 Cob, c6..c9 = 0. String: `2,25 1 1 0 0 0,1E 1 1 0 0 0,2A 1 2 1 0 0,1E 1 2 0 0 1,30 1 2 0 0 0,29 1 3 0 0 0,1E 1 3 0 0 1,30 1 3 0 0 0,2C 1 4 0 0 0,1E 1 4 0 0 1,30 1 4 0 0 0,2F 1 5 0 0 0,` (repeated for rows 2–5).

**D. rake example** "[DE] 守钉耙十一炮": `LI5H5DXMneRDXONmSDBUV9Xhxj9x4x6aMOTV3dQ8ncHGf5Wo1yTF4RRhvHYcPlTFqEd3BA==` — trailer high nibble nonzero (rake row); string begins `2,E 1 1 0 0 0,1E 1 1 0 0 0,25 1 2 0 0 0,2E 1 3 0 0 0,...` (contains `31 r 1 0 0 0` rake item). Gravestone example "[NE] 压碑五炮" string items like `32 1 6 1 3 0` (base=3).

## Reference Python (verified)
```python
def decode(code):
    b = bytes(x ^ 0x54 for x in base64.b64decode(code.replace('-','+').replace('_','/')))
    rake, scene = b[-1] >> 4, b[-1] & 15
    raw = zlib.decompress(b[:-1]); rows = 6 if scene in (2,3) else 5
    assert len(raw) == rows*18
    return scene, rake, [int.from_bytes(raw[i:i+2],'little') for i in range(0,len(raw),2)]
def encode(scene, rake, cells):  # cells: list of uint16, row-major
    rows = 6 if scene in (2,3) else 5
    raw = b''.join(x.to_bytes(2,'little') for x in cells[:rows*9])
    return base64.b64encode(bytes(x^0x54 for x in zlib.compress(raw,9)+bytes([(rake<<4)|scene]))).decode()
```
Seed type ids (0..47): 0 Peashooter,1 Sunflower,2 Cherry,3 Wall-nut,4 Potato Mine,5 Snow Pea,6 Chomper,7 Repeater,8 Puff,9 Sun-shroom,10 Fume,11 Grave Buster,12 Hypno,13 Scaredy,14 Ice-shroom,15 Doom,16 Lily Pad,17 Squash,18 Threepeater,19 Tangle Kelp,20 Jalapeno,21 Spikeweed,22 Torchwood,23 Tall-nut,24 Sea-shroom,25 Plantern,26 Cactus,27 Blover,28 Split Pea,29 Starfruit,30 Pumpkin,31 Magnet,32 Cabbage-pult,33 Flower Pot,34 Kernel-pult,35 Coffee Bean,36 Garlic,37 Umbrella Leaf,38 Marigold,39 Melon-pult,40 Gatling,41 Twin Sunflower,42 Gloom,43 Cattail,44 Winter Melon,45 Gold Magnet,46 Spikerock,47 Cob Cannon; pvztoolkit extras 48 Ladder, 49 Rake, 50 Gravestone.

## Unknowns
1) AvZ: the local AsmVsZombies checkout (2026-06) has no lineup-code parser or ASetPlants; the tutorial code string was not found by web search, so whether some AvZ version embeds a decoder is unverified (format assumed identical to pvztoolkit). 2) Lineup-string field 4 (0-4) semantics inferred from data (sun-shroom stage 2, gravestone variants 1-4) — not documented in source. STATE for gravestone (0-2) semantics unverified. 3) Exact mapping of plant seed-type names 0..47 is the standard PvZ order from memory, not re-verified against pvztoolkit data.cpp (though the test vectors are consistent: 2F=Cob, 2C=Winter Melon, E=Ice-shroom). 4) Codes are not byte-canonical across zlib implementations (21/339 differ after re-encoding with zlib-ng); pvztoolkit uses FLTK's bundled zlib. 5) jspvz.com / lonelystar.org ArrayDesign tools were not fetched; they may use a different format.

## Sources
- pvztoolkit src/lineup.cpp (raw): https://raw.githubusercontent.com/lmintlcx/pvztoolkit/master/src/lineup.cpp
- pvztoolkit inc/lineup.h (raw): https://raw.githubusercontent.com/lmintlcx/pvztoolkit/master/inc/lineup.h
- pvztoolkit src/utils.cpp base64 (raw): https://raw.githubusercontent.com/lmintlcx/pvztoolkit/master/src/utils.cpp
- pvz.tools 无尽名阵 lineup page: https://pvz.tools/lineup/
- lineup.yml (name -> code, 339 entries, 2020.12.04): https://pvz.lmintlcx.com/files/lineup.yml
- lineup_string.json (name -> lineup string): https://pvz.lmintlcx.com/files/lineup_string.json
- pvztoolkit repo tree: https://github.com/lmintlcx/pvztoolkit