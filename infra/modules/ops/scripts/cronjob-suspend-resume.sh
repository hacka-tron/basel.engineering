# glassbox-ops-cronjob-suspend / glassbox-ops-cronjob-resume: set
# spec.suspend on one known CronJob in the app namespace. Arguments:
# suspend|resume, then the CronJob: warm-answers.
#
# k8s/base/warm-cronjob.yaml doesn't set spec.suspend, so Flux doesn't own
# the field and leaves this change alone. Suspending doesn't stop a Job that
# is already running.

main() {
  exec </dev/null 2>&1
  set -euo pipefail
  local verb=${1:-} cronjob=${2:-} value
  require_one_of "$verb" suspend resume
  require_one_of "$cronjob" warm-answers
  if [ "$verb" = suspend ]; then value=true; else value=false; fi

  log "$verb cronjob/$cronjob in namespace app"
  kc -n app patch "cronjob/$cronjob" --type=merge --field-manager=glassbox-ops \
    -p "{\"spec\": {\"suspend\": $value} }"
  kc -n app get cronjob "$cronjob"
}
main "$@"
