param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
& gwap @Args
exit $LASTEXITCODE
