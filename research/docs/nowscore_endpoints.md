# NowScore.com Working Data Endpoints - Complete Report

## PRIMARY DATA SOURCES

### 1. Match Data (All Today's Matches)
- GET https://live.nowscore.com/data/bf1.js - 200 - MAIN DATA: All today's matches as JS arrays A[0]..A[n]
- GET https://live.nowscore.com/data/alias2.js - 200 - Team name aliases/translations
- GET https://live.nowscore.com/data/teamAlias.ashx?date=YYYY/MM/DD - 200 - Full team alias data
- GET https://live.nowscore.com/data/panlu.js - 200 - Historical handicap trend data

### 2. 1x2 European Odds Data
- GET https://1x2.nowscore.com/{matchId}.js - 200 - KEY: All 1x2 odds from all bookmakers
- GET https://live.nowscore.com/1x2/companies.js - 200 - All bookmaker company IDs and names

### 3. Asian Handicap + Over/Under Odds
- GET /odds/match/{matchId}.htm - 200 - Combined odds (AH + 1x2 + O/U) in HTML tables
- GET /odds/halfmatch/{matchId}.htm - 200 - Half-time odds page
- GET /odds/3in1Odds.aspx?companyid={cid}&id={matchId} - 200 - Company odds detail

### 4. Live Score Updates
- GET /football/GetLiveScore?scheid={matchId} - 200 - Live score (pipe-delimited text)
- WSS wss://ws.nowscore.com/stream?token={token} - WebSocket live updates
- GET /Special/GetSocketToken - 200 - JWT token for WebSocket
- GET /time.shtml - 200 - Server time sync

## SECONDARY ENDPOINTS
- GET /Odds/count/goalCount.aspx?t={type}&sid={matchId}&cid={companyId} - 200
- GET /odds/SearchAsianByTeamID.aspx?companyid={cid}&teamid={tid} - 200
- GET /odds/SearchSameAsian.aspx?companyid={cid}&goal={goal}&id1={t1}&id2={t2} - 200
- GET /odds/cornerDetail.aspx?id={matchId} - 200
- GET /1x2/OddsHistory.aspx?id={matchId} - 200
- GET /1x2/list.aspx?id={companyId}&rid={teamId} - 200
- GET /MatchDetail/{matchId}cn.html - 200
- GET /analysis/{matchId}cn.html - 200
- GET /panlu/{matchId}.html - 200
- GET /data/GetRecommend.aspx - 200
- GET /odds/today/sclassStats.aspx - 200

## KEY FINDINGS
1. No XML endpoints for odds - goal8.xml and ch_goal8.xml do not exist
2. Data format is JavaScript variables, not XML/JSON
3. Primary odds source: 1x2.nowscore.com/{matchId}.js and /odds/match/{matchId}.htm
4. Real-time via WebSocket wss://ws.nowscore.com/stream
5. Main match list from data/bf1.js with embedded handicap/O/U lines
6. pako_inflate.min.js used - WebSocket data may be gzip-compressed
