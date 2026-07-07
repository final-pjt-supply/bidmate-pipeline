<#
.SYNOPSIS
    realtime-dev-ds 버킷의 S3 이벤트 알림(raw/downloads/biz_div={업종}/realtime/ → SQS 3개)과
    각 큐의 "S3가 SendMessage 하도록 허용"하는 리소스 정책을 설정한다.

.DESCRIPTION
    ① 큐 3개(extract-pdf-queue / extract-hwp-queue / extract-hwpx-queue)에
       S3(realtime-dev-ds 버킷)가 SendMessage 할 수 있도록 허용하는 정책을 붙인다.
       (S3가 버킷 알림을 큐로 보내려면 이 정책이 먼저 있어야 함 — 없으면 ②에서 실패)
    ② 버킷 realtime-dev-ds에 이벤트 알림을 설정한다.
       key 구조가 raw/downloads/biz_div={업종}/{stage}/year=/month=/day=/... 라서
       (biz_div가 stage보다 앞) realtime만 트리거하려면 biz_div 값마다 규칙을 따로
       만들어야 한다. biz_div 4종(thng/servc/cnstwk/frgcpt) × 큐 3개 = 12개 규칙을
       prefix=raw/downloads/biz_div={업종}/realtime/ 로 생성하고, 확장자(suffix)로
       .pdf/.hwp/.hwpx를 각각 다른 큐로 라우팅한다. backfill(raw/.../backfill/)은
       prefix에 안 걸려서 트리거되지 않는다.

    ⚠ put-bucket-notification-configuration은 버킷의 알림 설정 "전체"를 교체한다.
      이 12개 규칙 외에 다른 알림이 버킷에 이미 설정돼 있다면 이 스크립트 실행 후 사라진다.
      (이 스크립트만 반복 실행하는 건 멱등 — 같은 규칙으로 매번 덮어씀)

.NOTES
    JSON을 커맨드라인 인자로 직접 넘기면 PowerShell 따옴표 이스케이프 문제가 잦다.
    그래서 BOM 없는 UTF-8 파일로 저장한 뒤 절대경로 file:// 로 넘기는 방식을 쓴다
    (PowerShell 5.1의 -Encoding utf8은 BOM을 붙이는데, aws cli/botocore가 이 BOM을
    JSON 파싱 에러로 취급하는 경우가 있어 .NET WriteAllText로 직접 BOM 없이 쓴다).

.EXAMPLE
    ./setup-s3-events.ps1
    ./setup-s3-events.ps1 -Bucket realtime-dev-ds -Region ap-northeast-2
#>

param(
    [string]$Region   = "ap-northeast-2",
    [string]$Account  = "890608337282",
    [string]$Bucket   = "realtime-dev-ds",
    [string]$Stage    = "realtime",
    [string[]]$BizDivs = @("thng", "servc", "cnstwk", "frgcpt")
)

$ErrorActionPreference = "Stop"

$bucketArn = "arn:aws:s3:::$Bucket"

# 큐별 설정 — Id는 S3 NotificationConfiguration 안에서 유일해야 하는 식별자
$queues = @(
    @{ Name = "extract-pdf-queue";  Suffix = ".pdf";  Id = "ExtractPdfOnRawUpload" }
    @{ Name = "extract-hwp-queue";  Suffix = ".hwp";  Id = "ExtractHwpOnRawUpload" }
    @{ Name = "extract-hwpx-queue"; Suffix = ".hwpx"; Id = "ExtractHwpxOnRawUpload" }
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
# ② S3 버킷 이벤트 알림 설정
#    biz_div 4종 × 큐 3개 = 규칙 12개. prefix가 biz_div마다 달라야 해서 이중 순회.
# ============================================================
$notificationConfig = @{
    QueueConfigurations = foreach ($biz in $BizDivs) {
        foreach ($q in $queueConfigs) {
            @{
                Id       = "$($q.Id)_$biz"
                QueueArn = $q.Arn
                Events   = @("s3:ObjectCreated:*")
                Filter   = @{
                    Key = @{
                        FilterRules = @(
                            @{ Name = "prefix"; Value = "raw/downloads/biz_div=$biz/$Stage/" }
                            @{ Name = "suffix"; Value = $q.Suffix }
                        )
                    }
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

Write-Host "버킷 $Bucket 이벤트 알림 설정 완료 (biz_div $($BizDivs.Count)종 x 큐 3개 = 규칙 $($BizDivs.Count * $queueConfigs.Count)개, stage=$Stage)"
