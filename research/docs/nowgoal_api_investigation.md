# NowGoal/NowScore (捷报比分网) Data API Investigation

## CRITICAL FINDING: nowgoal3.com is DEAD
- nowgoal3.com is a **parked domain** for sale on HugeDomains ($3,195)
- It has NO sports data whatsoever
- The ACTUAL working site is **nowscore.com** (same company, 捷报比分网)

## Working Domain Structure
- **www.nowscore.com** - Main portal/news (237KB)
- **live.nowscore.com** - Live scores + odds (the data hub)
- **api.nowscore.com** - API endpoint (requires login for some endpoints)
- **info.nowscore.com** - Team/league info, images
- **guess.nowscore.com** - Tips/predictions
- **pic.nowscore.com** - Image CDN
- **m.nowscore.com** - Mobile version
- **ws.nowscore.com** - WebSocket server

## DATA ENDPOINTS (live.nowscore.com)

### 1. WebSocket Real-time Feed
- **URL**: `wss://ws.nowscore.com/stream?token=<JWT>`
- **Token**: GET `/Special/GetSocketToken` (returns JWT, no auth required)
- **Channels**:
  - `change_xml` - Live score changes (goals, cards, status)
  - `ch_goal{companyID}_xml` - Real-time odds changes per bookmaker
- **Data format**: gzip-compressed JSON, parsed with pako.inflate
- **WebSocket message format**: `{"change_xml": "data1!data2!...", "ch_goal3_xml": "odds1!odds2!..."}`
- **Score change format**: `matchID^state^score1^score2^half1^half2^card1^card2^time1^time2^explain^lineup`
- **This IS real-time rolling odds** - pushed via WebSocket as they change!

### 2. XML Data Files (Polling Fallback)
When WebSocket fails, falls back to polling these files every 2-3 seconds:

#### Score Changes:
- `data/change.xml` - Incremental score changes (polled every 2s)
- `data/change2.xml` - Full refresh (polled every 60th cycle)

#### Initial Match Data:
- `data/bf1.js` - All matches sorted by league (242+ matches, ~75KB)
- `data/bf.js` - All matches sorted by time
- Format: JavaScript arrays `A[0]=[matchId, leagueIdx, teamId1, teamId2, 'teamName_cn', 'teamName_tc', 'teamName_en', ...]`
- Contains: match ID, league, teams (3 languages), time, score, cards, handicap line, over/under line

#### Initial Odds Data:
- `data/goal{companyID}.xml` - Full odds snapshot for a bookmaker (~5-22KB)
- Format: XML with `<match><m>` elements
- Each `<m>`: `matchID, asianOddsID, handicap, homeOdds, awayOdds, euroOddsID, homeWin, draw, awayWin, ouOddsID, totalLine, overOdds, underOdds, state, ...`

#### Real-time Odds Changes:
- `data/ch_goal{companyID}.xml` - Odds changes for specific bookmaker (polled every 3s)
- `data/ch_sbOdds.xml` - Odds changes for "SB" bookmaker (default)
- `data/ch_runOdds_{companyID}.xml` - Running odds with in-play sub-odds (Asian + Euro + O/U, full + half)

### 3. Bookmaker Company IDs
| ID | Company |
|----|---------|
| 1  | Macau (澳门) |
| 3  | Bet365 |
| 4  | Ladbrokes |
| 8  | Crown/Pinnacle (皇冠) |
| 12 | Interwetten |
| 17 | 10Bet |
| 24 | 188Bet |
| 31 | Unknown |

### 4. Match Analysis Data
- `/analysisJs/data{matchID}.js` - Full match analysis (standings, H2H, ~114KB)
- `/data/panlu.js` - Historical handicap results for all matches (~62KB)
- `/data/detail.js` - Match event details (goals, cards, substitutions)
- `/data/alias2.js` - Team name aliases

### 5. Odds Detail Pages
- `/odds/match/{matchID}.htm` - All bookmakers' odds for a match
- `/odds/3in1Odds.aspx?companyid={id}&id={matchID}` - Asian + Euro + O/U trends for specific bookmaker
- `/1x2/{matchID}.htm` - 1x2 odds comparison
- `/odds/today/sclassStats.aspx` - Today's league stats (handicap + O/U records)

### 6. API Endpoints
- `api.nowscore.com/tool/getnotice?id={id}` - Notifications
- Returns JSON: `{"Result":false,"Msg":"未登录"}` for unauthenticated requests

## DATA FORMAT DETAILS

### Match Array (A[]) Fields:
```
A[i] = [
  0: matchID,
  1: leagueIndex,
  2: homeTeamID,
  3: awayTeamID,
  4: homeTeam_CN,
  5: homeTeam_TC,
  6: homeTeam_EN,
  7: awayTeam_CN,
  8: awayTeam_TC,
  9: awayTeam_EN,
  10: time,
  11: dateObj,
  12: state (-1=live, 0=not started, etc.),
  13: homeScore,
  14: awayScore,
  ...
  25: handicapLine (e.g. 0.75, -0.25),
  ...
  30: overUnderLine (e.g. 2.5),
  ...
]
```

### Odds XML (goal{id}.xml) Fields:
```
matchID, asianOddsID, handicapLine, homeOdds, awayOdds,
euroOddsID, homeWin, draw, awayWin,
ouOddsID, totalLine, overOdds, underOdds,
matchState, liveHomeScore, liveAwayScore, ?,
asianHalfFlag, ouHalfFlag,
[many empty fields for half-time odds],
initialHandicap, initialTotal
```

## KEY FINDINGS

### ✅ Real-time Rolling Odds: YES!
- WebSocket channel `ch_goal{companyID}_xml` pushes odds changes in real-time
- Fallback: XML polling every 3 seconds for `ch_goal{companyID}.xml`
- The `ch_sbOdds.xml` provides running odds with full/half-time breakdowns
- This is BETTER than titan007's change-snapshot approach

### ✅ No Login Required for Core Data
- WebSocket token: `/Special/GetSocketToken` returns JWT without authentication
- All data files (bf1.js, goal*.xml, change*.xml) are publicly accessible
- API subdomain requires login for some features but not needed for scores/odds

### ✅ Comprehensive Odds Coverage
- Asian Handicap (亚盘/让球)
- Over/Under (大小球)
- European 1x2 (欧赔)
- Corner odds (角球)
- Multiple bookmakers supported
- Both full-time and half-time odds

### ✅ Data Freshness
- WebSocket: instant push
- XML polling: 2-3 second intervals
- Match data refresh: hourly

### Architecture vs titan007:
| Feature | titan007 | nowscore |
|---------|----------|----------|
| Live scores | bfdata_ut.js (polling) | WebSocket + change.xml fallback |
| Odds data | scoreOdds_bf.txt (snapshots) | WebSocket + ch_goal*.xml (real-time) |
| Odds format | Change snapshots | Rolling real-time values |
| Protocol | HTTP polling | WebSocket primary, HTTP fallback |
| Auth | None | None for core data |
| Companies | Multiple | 7+ bookmakers |
