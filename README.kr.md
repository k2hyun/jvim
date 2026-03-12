# jvim

[Textual](https://github.com/Textualize/textual) 기반의 vim 스타일 JSON 편집기입니다.

## 스크린샷

### 편집기
![jvim](docs/jvim.svg)

### Diff 뷰어
![jvimdiff](docs/jvimdiff.svg)

## 주요 기능

- **Vim 스타일 모달 편집** - Normal, Insert, Command, Search 모드 지원
- **구문 강조** - JSON 문법에 맞는 색상 표시
- **JSON 검증** - 실시간 유효성 검사 및 오류 표시
- **JSONPath 검색** - JSONPath 표현식으로 검색 (`$.foo.bar`)
- **단어 검색** - 커서 위치 단어를 `*`(정방향) 및 `#`(역방향)으로 검색
- **JSONL 지원** - JSON Lines 파일을 스마트하게 포맷팅하여 편집
- **내장 JSON 편집** - JSON 내 문자열로 저장된 JSON을 중첩 레벨까지 편집
- **Visual 모드** - 문자 단위(`v`) 및 줄 단위(`V`) 선택 후 `d`/`y`/`c` 연산자 지원
- **숫자 접두사(Count prefix)** - Vim 스타일 숫자 접두사(`5j`, `3dd`, `d3d`) 및 fold-aware 줄 조작
- **접기(Folding)** - JSON 블록과 긴 문자열 값을 접기/펼치기
- **괄호 매칭** - `%`로 짝이 맞는 괄호로 이동
- **Undo/Redo** - 전체 실행 취소 기록 지원
- **치환(Substitute)** - Vim 스타일 `:s/old/new/g` 정규식, 범위, 플래그 지원
- **멀티 파일 탐색** - 여러 파일을 열고 `:n`, `:N`, `:e#`으로 전환
- **Diff 뷰어** - 스크롤/접기 동기화가 적용된 나란히 JSON 비교

## 설치

```bash
pip install jvim
```

## 사용법

```bash
# 파일 열기
jvim data.json

# 여러 파일 열기
jvim a.json b.json c.json

# 읽기 전용 모드로 열기
jvim -R data.json

# 새 파일 생성
jvim newfile.json
```

`jvi`, `jv` 단축 명령도 사용 가능합니다.

## JSONL 지원

jvim은 JSON Lines (`.jsonl`) 파일을 특별하게 처리합니다:

- **포맷팅된 편집**: 각 JSONL 레코드를 자동으로 들여쓰기하여 읽고 편집하기 쉽게 표시
- **압축 저장**: 저장 시 각 레코드를 한 줄로 압축하여 JSONL 형식 유지
- **레코드 번호**: 두 번째 열에 레코드 번호(1, 2, 3...)를 표시하여 쉽게 탐색
- **플로팅 헤더**: 여러 줄에 걸친 레코드를 스크롤할 때 물리적 라인 번호가 상단에 표시

예시: 두 개의 레코드가 있는 JSONL 파일:
```
{"name": "Alice", "age": 30}
{"name": "Bob", "age": 25}
```

jvim에서 열면:
```
{
    "name": "Alice",
    "age": 30
}
{
    "name": "Bob",
    "age": 25
}
```

저장하면 원래의 압축된 형식으로 복원됩니다.

## JSONPath 검색

jvim은 값 필터링이 가능한 강력한 JSONPath 검색을 지원합니다.

### 기본 JSONPath

`$.` 또는 `$[`로 시작하는 검색 패턴은 자동으로 JSONPath로 인식됩니다:

```
/$.name              # "name" 필드 찾기
/$..email            # 모든 "email" 필드 찾기 (재귀)
/$.users[0]          # 첫 번째 사용자
/$.users[*].name     # 모든 사용자 이름
```

### 값 필터링

비교 연산자를 사용하여 값으로 검색 결과를 필터링할 수 있습니다:

| 연산자 | 설명 | 예시 |
|--------|------|------|
| `=` | 같음 | `$.status="active"` |
| `!=` | 다름 | `$.status!=null` |
| `>` | 초과 | `$.age>18` |
| `<` | 미만 | `$.price<100` |
| `>=` | 이상 | `$.count>=5` |
| `<=` | 이하 | `$.count<=10` |
| `~` | 정규식 매칭 | `$.email~@gmail\.com$` |

### 예시

```
/$.users[*].age>30           # 30세 초과 사용자
/$.items[*].status="active"  # 활성 상태인 아이템
/$..name~^J                  # J로 시작하는 모든 이름
/$.price<=1000               # 1000 이하 가격
/$.config.enabled=true       # 활성화된 설정
```

### 검색 수정자

| 접미사 | 설명 |
|--------|------|
| `\j` | 모호한 패턴을 JSONPath 모드로 강제 |
| `\c` | 대소문자 무시 (정규식 텍스트 검색용) |
| `\C` | 대소문자 구분 (정규식 텍스트 검색용) |

### 히스토리

검색 및 명령어 히스토리가 `~/.jvim/history.json`에 자동 저장되며 다음 실행 시 복원됩니다. 검색(`/`) 및 명령(`:`) 모드에서 화살표 키(`↑`/`↓`)로 히스토리를 탐색할 수 있습니다.

## 내장 JSON 편집 (ej 모드)

JSON 파일에는 종종 이스케이프된 JSON 문자열이 값으로 포함됩니다. jvim은 이러한 중첩된 JSON 구조를 자연스럽게 편집할 수 있게 해줍니다.

### 사용 방법

1. JSON 문자열 값이 있는 라인에 커서를 위치
2. Normal 모드에서 `ej` 입력
3. 새 편집기 패널이 열리며 파싱되고 포맷팅된 JSON 표시
4. 구문 강조와 유효성 검사가 적용된 상태로 내장 JSON 편집
5. `:w`로 저장하여 부모 문서에 반영(압축됨) 또는 `:q`로 취소

### 중첩 레벨

내장 JSON 안의 내장 JSON도 편집할 수 있습니다:
- 패널 제목에 현재 중첩 레벨 표시: `Edit Embedded JSON (level 1)`
- 저장하지 않은 변경 사항이 있으면 `[+]` 표시
- `:w`로 부모 문서에 저장하고 편집 계속
- `:wq`로 저장하고 이전 레벨로 복귀
- `:q!`로 변경 사항을 버리고 이전 레벨로 복귀

### 예시

다음과 같은 JSON이 있을 때:
```json
{
    "config": "{\"host\": \"localhost\", \"port\": 8080}"
}
```

config 라인에서 `ej`를 사용하면:
```json
{
    "host": "localhost",
    "port": 8080
}
```

편집 후 저장하면, 부모 문서에 압축된 결과가 반영됩니다.

## 치환

jvim은 vim 스타일의 치환 명령으로 찾기-바꾸기를 지원합니다.

| 명령 | 설명 |
|------|------|
| `:s/old/new/` | 현재 라인에서 첫 번째 매치 치환 |
| `:s/old/new/g` | 현재 라인에서 모든 매치 치환 |
| `:%s/old/new/g` | 전체 파일에서 모든 매치 치환 |
| `:N,Ms/old/new/g` | N~M 라인 범위에서 모든 매치 치환 |

### 기능

- **커스텀 구분자** - 임의의 문자를 구분자로 사용 가능: `:s#old#new#g`, `:s|old|new|g`
- **플래그** - `g` (전역/모든 매치), `i` (대소문자 무시)
- **정규식 지원** - 그룹 캡처(`\1`, `\2`) 포함 정규 표현식
- **Undo** - 모든 치환은 `u`로 실행 취소 가능

### 예시

```
:s/foo/bar/           # 현재 라인에서 첫 번째 "foo"를 "bar"로 치환
:%s/TODO/DONE/g       # 전체 파일에서 "TODO"를 "DONE"으로 치환
:s/(\w+)/[\1]/g       # 각 단어를 대괄호로 감싸기
:%s/old/new/gi        # 대소문자 무시하여 전체 파일에서 치환
:2,10s/from/to/g      # 2~10번 라인에서 치환
```

### JSONPath 치환

검색 패턴이 `$.` 또는 `$[`로 시작하면 JSONPath 기반으로 JSON 구조를 치환합니다. 끝의 `=` 유무로 모드가 결정됩니다:

| 패턴 | 모드 | 설명 |
|------|------|------|
| `:s/$.key/newkey/g` | 키 변경 | JSON 키 이름 변경 |
| `:s/$.key=/value/g` | 값 치환 | 경로의 모든 값 치환 |
| `:s/$.key="old"/new/g` | 조건부 값 | 필터 조건에 맞는 값만 치환 |

#### 키 이름 변경

```
:s/$.name/username/g             # "name" 키를 "username"으로 변경
:s/$..name/label/g               # 모든 "name" 키를 재귀적으로 변경
```

#### 값 치환

```
:s/$.name=/Bob/g                 # "name" 값을 "Bob"으로 치환
:s/$..status=/active/g           # 모든 "status" 값을 재귀적으로 치환
:s/$.users[*].age=/0/g          # 모든 사용자 나이를 0으로 초기화
:s/$..enabled=/true/g            # 모든 "enabled"를 true로 설정
:s/$..status="draft"/review/g   # "draft" 상태만 필터링하여 치환
```

대체 값은 자동 감지됩니다: 숫자(`42`), 불리언(`true`/`false`), `null`은 그대로 사용되며, 나머지는 JSON 문자열로 인코딩됩니다.

## 멀티 파일 탐색

여러 파일을 열고 argument list 명령으로 파일 간 이동할 수 있습니다:

```bash
jvim a.json b.json c.json
```

| 명령 | 설명 |
|------|------|
| `:n` | 다음 파일로 이동 |
| `:N` | 이전 파일로 이동 |
| `:e#` | 대체(이전에 열었던) 파일로 전환 |

타이틀 바에 현재 파일 리스트 위치가 표시됩니다: `a.json [1/3]`.

`:e <file>`로 연 파일은 대체 파일로 기록되어 `:e#`으로 돌아갈 수 있습니다.

## Git Difftool 연동

jvimdiff를 `git difftool`로 사용하여 저장소의 JSON 변경사항을 비교할 수 있습니다.

### 설정

```bash
# jvimdiff를 git difftool로 등록
jvimdiff --install-difftool

# 등록 제거
jvimdiff --uninstall-difftool
```

### 사용법

```bash
# jvimdiff로 비교
git difftool -t jvimdiff              # 모든 변경 파일
git difftool -t jvimdiff file.json    # 특정 파일
git difftool -t jvimdiff HEAD~3       # 특정 커밋과 비교

# 기본 difftool로 설정
git config --global diff.tool jvimdiff
git difftool file.json                # -t 플래그 없이 사용
```

## Diff 뷰어

jvim에는 두 JSON 파일을 나란히 비교하는 diff 뷰어가 포함되어 있습니다.

### 사용법

```bash
# 두 JSON 파일 비교
jvimdiff file1.json file2.json

# JSON 정규화 건너뛰기 (원본 텍스트 비교)
jvimdiff --no-normalize file1.json file2.json

# JSONL 파일 비교 (.jsonl 확장자는 자동 감지)
jvimdiff --jsonl file1.json file2.json
```

`jvd` 단축 명령도 사용 가능합니다.

### 기능

- **색상 구분 diff** - 삭제(빨간색), 삽입(초록색), 변경(회색)이 하이라이트됨
- **스크롤 동기화** - 양쪽 패널이 함께 스크롤
- **접기 동기화** - 한쪽 패널에서 접기/펼치기 시 다른 쪽에도 동일하게 적용
- **자동 접기** - 로드 시 전체 구조를 접고, diff가 있는 부분만 자동으로 펼침
- **Hunk 탐색** - `]c` 다음 hunk, `[c` 이전 hunk (순환)
- **패널 전환** - `Tab`으로 좌우 패널 간 포커스 전환
- **활성 패널 표시** - 타이틀 바로 포커스된 패널을 강조 표시
- **커서 동기화** - 양쪽 패널의 커서 위치가 동기화
- **라인 번호** - 논리적 라인 번호(왼쪽)와 JSONL 레코드 번호(양쪽 패널)
- **내장 JSON diff** - `ej`로 각 패널의 내장 JSON 문자열을 비교

## 키 바인딩

jvim 내에서 `:help`를 입력하면 전체 키 바인딩을 확인할 수 있습니다.

## Vim 플러그인

동일한 JSON 편집 기능(폴딩, JSONPath, 내장 JSON, JSONL)을 제공하는 네이티브 Vim 플러그인이 별도 리포지토리로 제공됩니다:

[vim-jvim](https://github.com/k2hyun/vim-jvim) — 원하는 플러그인 매니저로 설치:

```vim
Plug 'k2hyun/vim-jvim'
```

## 라이선스

MIT
