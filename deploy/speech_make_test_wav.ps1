param(
    [string]$OutputPath = 'models\speech-test-synthetic.wav'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$destination = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputPath))
if (Test-Path $destination) {
    throw 'Refusing to overwrite an existing fixture. Choose a new OutputPath.'
}
New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetDirectoryName($destination)) | Out-Null
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
        32000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono
    )
    $voice.Rate = 0
    $voice.SetOutputToWaveFile($destination, $format)
    $voice.Speak('Hello world. Today we are practicing a short presentation. Please speak clearly and take your time. Our project helps people improve their speaking skills. Thank you for listening to this example.')
}
finally {
    $voice.Dispose()
}
Write-Output "Synthetic TEST ONLY fixture: $destination (mono PCM16, 32000 Hz; microphone never opened)"
