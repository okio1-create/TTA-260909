# TTA-260909 · Flask 파일 관리 앱

Python Flask로 만든 파일 관리 웹앱입니다. 파일 업로드(드래그 앤 드롭, 다중), 목록/검색, 미리보기, 다운로드, 이름 변경, 삭제를 지원합니다.

- 로컬 실행 시: 파일은 `data/uploads/`, 메타데이터는 `data/files.db`(SQLite)에 저장
- Vercel + Supabase: 파일은 Supabase Storage(`files` 버킷), 메타데이터는 Postgres `files` 테이블에 저장

## 로컬 실행

```bash
pip install -r requirements.txt
python app.py          # http://127.0.0.1:5000
python -m pytest -q    # 테스트
```

## 환경 변수

| 변수 | 설명 |
|---|---|
| `SUPABASE_URL` | Supabase 프로젝트 URL (`https://<ref>.supabase.co`) |
| `SUPABASE_SERVICE_KEY` | Supabase service_role 키 (서버 전용) |
| `SUPABASE_BUCKET` | Storage 버킷 이름 (기본 `files`) |
| `SUPABASE_TABLE` | 메타데이터 테이블 (기본 `files`) |
| `MAX_UPLOAD_BYTES` | 업로드 최대 크기 (기본 4 MB, Vercel 요청 제한 4.5 MB) |
| `FILE_MANAGER_DATA` | 로컬 백엔드 데이터 폴더 (기본 `./data`) |

두 Supabase 변수가 모두 설정되면 자동으로 Supabase 백엔드를 사용하고, 없으면 로컬 백엔드를 사용합니다.

## Supabase 설정

`supabase/schema.sql`을 SQL Editor에서 실행하면 테이블과 비공개 버킷이 생성됩니다.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/files?q=` | 파일 목록 (이름 검색) |
| POST | `/api/files` | 업로드 (multipart, 필드명 `file`, 다중 가능) |
| GET | `/api/files/<id>` | 메타데이터 |
| GET | `/api/files/<id>/download` | 다운로드 |
| GET | `/api/files/<id>/preview` | 인라인 미리보기 |
| PATCH | `/api/files/<id>` | 이름 변경 `{"name": "..."}` |
| DELETE | `/api/files/<id>` | 삭제 |
| GET | `/api/stats` | 파일 수 / 총 용량 |
| GET | `/health` | 상태 확인 |

## 배포

`vercel.json`이 포함되어 있어 Vercel에 그대로 배포됩니다. Vercel 프로젝트 환경 변수에 `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`를 추가하세요.
