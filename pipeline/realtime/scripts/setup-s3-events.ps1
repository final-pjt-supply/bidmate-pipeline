<#
.SYNOPSIS
    bidmate 버킷의 S3 이벤트 알림(raw/downloads/daily/ → SQS 3개, 확장자별 라우팅)과
    각 큐의 "S3가 SendMessage 하도록 허용"하는 리소스 정책을 설정한다.

.DESCRIPTION
    ① 큐 3개(realtime-extract-pdf-queue / realtime-extract-hwp-queue / realtime-extract-hwpx-queue)에
       S3(bidmate 버킷)가 SendMessage 할 수 있도록 허용하는 정책을 붙인다.
       (S3가 버킷 알림을 큐로 보내려면 이 정책이 먼저 있어야 함 — 없으면 ②에서 실패)
    ② 버킷 bidmate에 이벤트 알림을 설정한다. 규칙은 딱 3개 — 확장자(suffix)로만
       .pdf/.hwp/.hwpx를 각각 다른 큐로 라우팅하고, prefix는 raw/downloads/daily/
       까지만 건다(biz_div 값은 필터에 안 넣음).

       ⚠ 접두사에 "=" 문자를 넣지 말 것 (2026-07-08 트러블슈팅으로 확정된 원인).
       원래 biz_div 4종(thng/servc/cnstwk/frgcpt) × 큐 3개 = 12개 규칙을
       prefix=raw/downloads/daily/biz_div={업종}/ 로 만들었었는데, S3가 알림 필터의
       prefix 값에 들어있는 "="를 URL 인코딩된 형태로 저장해버려서 실제 업로드되는
       키(원문 그대로 "=" 포함)와 매칭이 안 되고 이벤트가 전혀 발생하지 않았다.
       (여러 규칙이 동시에 있을 때는 반대로 필터가 서로 오염돼 전혀 무관한 키까지
       큐로 새는 별도 증상도 관찰됨 — 두 증상 다 "=" 포함 다중 규칙 조합에서만 발생)
       해결: prefix에서 "=" 자체를 빼고 raw/downloads/daily/까지만 걸고,
       biz_div 구분은 안 하고 확장자(suffix)만으로 큐를 나눈다. biz_div 값 자체는
       common/paths.py가 key를 파싱할 때 그대로 읽으므로 필터에서 안 걸러도 무방하다.
       이 prefix는 raw/downloads/daily/ 로 시작하는 것만 걸리므로 raw/raw/,
       raw/curated/(공고 메타데이터, 첨부파일 아님), raw/downloads/backfill/
       (별도 파이프라인인 backfill_lambda 소관)는 구조적으로 안 걸린다. 다만
       raw/downloads/daily/_metadata/(다운로드 매니페스트 JSON)는 prefix까진 걸리고
       suffix(.json)가 안 맞아서 걸러진다.

    ⚠ put-bucket-notification-configuration은 버킷의 알림 설정 "전체"를 교체한다.
      이 3개 규칙 외에 다른 알림이 버킷에 이미 설정돼 있다면 이 스크립트 실행 후 사라진다.
      (이 스크립트만 반복 실행하는 건 멱등 — 같은 규칙으로 매번 덮어씀)

.NOTES
    JSON을 커맨드라인 인자로 직접 넘기면 PowerShell 따옴표 이스케이프 문제가 잦다.
    그래서 BOM 없는 UTF-8 파일로 저장한 뒤 절대경로 file:// 로 넘기는 방식을 쓴다
    (PowerShell 5.1의 -Encoding utf8은 BOM을 붙이는데, aws cli/botocore가 이 BOM을
    JSON 파싱 에러로 취급하는 경우가 있어 .NET WriteAllText로 직접 BOM 없이 쓴다).

.EXAMPLE
    ./setup-s3-events.ps1
    ./setup-s3-events.ps1 -Bucket bidmate -Region ap-northeast-2
#>

param(
    [string]$Region  = "ap-northeast-2",
    [string]$Account = "890608337282",
    [string]$Bucket  = "bidmate",
    [string]$Prefix  = "raw/downloads/daily/"
)

$ErrorActionPreference = "Stop"

$bucketArn = "arn:aws:s3:::$Bucket"

# 큐별 설정 — Id는 S3 NotificationConfiguration 안에서 유일해야 하는 식별자
# 2026-07-08 인프라 재구축(realtime- 접두사 명명)으로 큐 이름이 바뀜 — template.yaml 참고.
$queues = @(
    @{ Name = "realtime-extract-pdf-queue";  Suffix = ".pdf";  Id = "ExtractPdfOnRawUpload" }
    @{ Name = "realtime-extract-hwp-queue";  Suffix = ".hwp";  Id = "ExtractHwpOnRawUpload" }
    @{ Name = "realtime-extract-hwpx-queue"; Suffix = ".hwpx"; Id = "ExtractHwpxOnRawUpload" }
)

$tmpDir = Join-Path $env:TEMP "bidding-agent-s3-events"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

# ============================================================
# ① 큐별 URL/ARN 조회 + S3 SendMessage 허용 정책 적용
# ============================================================
$queueConfigs = @()   # ②에서 ARN을 재사용하기 위해 누적

foreach ($q in $queues) {
    $queueUrl = aws sqs get-queue-url `
        --queue-name $q.Name `
        --region $Region `
        --query "QueueUrl" --output text

    $queueArn = aws sqs get-queue-attributes `
        --queue-url $queueUrl `
        --attribute-names QueueArn `
        --region $Region `
        --query "Attributes.QueueArn" --output text

    Write-Host "[$($q.Name)] URL=$queueUrl ARN=$queueArn"

    $policy = @{
        Version   = "2012-10-17"
        Statement = @(
            @{
                Sid       = "AllowS3BucketToSendMessage"
                Effect    = "Allow"
                Principal = @{ Service = "s3.amazonaws.com" }
                Action    = "SQS:SendMessage"
                Resource  = $queueArn
                Condition = @{
                    ArnEquals    = @{ "aws:SourceArn" = $bucketArn }
                    StringEquals = @{ "aws:SourceAccount" = $Account }
                }
            }
        )
    } | ConvertTo-Json -Depth 10 -Compress

    $attributes = @{ Policy = $policy } | ConvertTo-Json -Depth 10 -Compress
    $attributesPath = Join-Path $tmpDir "$($q.Name)-attributes.json"
    Write-Utf8NoBom -Path $attributesPath -Content $attributes

    aws sqs set-queue-attributes `
        --queue-url $queueUrl `
        --attributes "file://$attributesPath" `
        --region $Region

    Write-Host "[$($q.Name)] SendMessage 정책 적용 완료"

    $queueConfigs += @{ Name = $q.Name; Arn = $queueArn; Suffix = $q.Suffix; Id = $q.Id }
}

# ============================================================
# ② S3 버킷 이벤트 알림 설정 — 큐 3개, 규칙 3개. prefix엔 "=" 절대 넣지 않는다.
# ============================================================
$notificationConfig = @{
    QueueConfigurations = foreach ($q in $queueConfigs) {
        @{
            Id       = $q.Id
            QueueArn = $q.Arn
            Events   = @("s3:ObjectCreated:*")
            Filter   = @{
                Key = @{
                    FilterRules = @(
                        @{ Name = "prefix"; Value = $Prefix }
                        @{ Name = "suffix"; Value = $q.Suffix }
                    )
                }
            }
        }
    }
} | ConvertTo-Json -Depth 10 -Compress

$notificationPath = Join-Path $tmpDir "notification-configuration.json"
Write-Utf8NoBom -Path $notificationPath -Content $notificationConfig

aws s3api put-bucket-notification-configuration `
    --bucket $Bucket `
    --notification-configuration "file://$notificationPath" `
    --region $Region

Write-Host "버킷 $Bucket 이벤트 알림 설정 완료 (큐 3개 = 규칙 3개, prefix=$Prefix)"
