# 배포 가이드 — GitHub + Vercel

## 0. 사전 준비 (1회)
- GitHub 계정, [vercel.com](https://vercel.com) 가입(GitHub로 로그인)
- 정적 데이터 생성 (국내 IP 필요 → 본인 PC에서):
  ```bash
  python3 scripts/build_static.py     # → data/static-lots.json 생성
  ```

## 1. GitHub에 푸시
```bash
cd "모두의공영주차장 폴더"
git init
git add -A
git commit -m "모두의공영주차장 v1"
```
GitHub 웹에서 새 저장소 `modu-parking` 생성(README 없이) 후:
```bash
git remote add origin https://github.com/<본인아이디>/modu-parking.git
git branch -M main
git push -u origin main
```
(gh CLI가 있으면: `gh repo create modu-parking --public --source=. --push` 한 줄)

※ `config.json`(인증키)은 .gitignore로 제외되어 올라가지 않음 — 정상.

## 2. Vercel 배포
1. vercel.com → **Add New → Project** → `modu-parking` 저장소 Import
2. **Environment Variables**에 추가: `SEOUL_KEY` = 서울 열린데이터광장 인증키
3. **Deploy** 클릭 → 1~2분 후 `https://modu-parking.vercel.app` 완성
4. 이후엔 `git push`만 하면 자동 재배포

구조: 화면(index.html)은 정적 서빙, `/api/parking`은 서울리전(icn1) 서버리스가
서울시 실시간 API 호출 + 커밋된 정적 스냅샷(data/static-lots.json)과 병합.
data.go.kr은 해외 IP를 차단하므로 서버리스에서 직접 부르지 않고 스냅샷 방식 사용.

## 3. 도메인
- **무료**: `modu-parking.vercel.app` 즉시 사용 가능 (https 자동)
- **커스텀 도메인** (연 1~2만원):
  1. 가비아/후이즈/Cloudflare 등에서 도메인 구매 (예: moduparking.kr)
  2. Vercel 프로젝트 → Settings → **Domains** → 도메인 입력
  3. 안내대로 구매처에 A레코드(76.76.21.21) 또는 CNAME(cname.vercel-dns.com) 등록
  4. 몇 분~몇 시간 내 적용, https 인증서 자동 발급

## 4. 운영 루틴
| 주기 | 작업 |
|---|---|
| 자동 | 실시간 데이터 (5분 캐시, 서버리스가 처리) |
| 월 1회 | `python3 scripts/build_static.py` → `git add data && git commit && git push` (요금·신규 주차장 갱신) |

## 로컬 개발 (배포와 무관하게 계속 사용 가능)
```bash
python3 server.py   # → http://localhost:8765
```
