param([string]$Json, [string]$OutDir)
Add-Type -AssemblyName System.Speech
$items = Get-Content -Raw -Encoding UTF8 $Json | ConvertFrom-Json
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('Microsoft Zira Desktop')
$s.Rate = -1
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($i in $items) {
  $s.SetOutputToWaveFile((Join-Path $OutDir ($i.id + '.wav')), $fmt)
  $s.Speak($i.say)
}
$s.Dispose()
