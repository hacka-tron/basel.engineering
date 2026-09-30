# Compressed-RAM (zram) swap on the node, configured through SSM State
# Manager rather than user_data: user_data only runs on first boot, and
# changing it on aws_instance.glassbox forces a stop/start. The association
# runs zram-swap.sh once on creation, again whenever the document changes,
# and weekly so drift self-heals. The script is idempotent and never turns
# swap off. Why and how to check it: docs/DESIGN.md section 9.7.
#
# Run output stays in SSM (association execution history / Run Command
# output); no S3 bucket or CloudWatch log group is added for it.

resource "aws_ssm_document" "zram_swap" {
  name            = "glassbox-zram-swap"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Enable zram swap (priority 100) ahead of /swapfile on the Glassbox k3s node. Idempotent."
    parameters = {
      mode = {
        type           = "String"
        description    = "apply, or dry-run to print what would change."
        default        = "apply"
        allowedValues  = ["apply", "dry-run"]
        allowedPattern = "^(apply|dry-run)$"
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "zramSwap"
      precondition = {
        StringEquals = ["platformType", "Linux"]
      }
      inputs = {
        timeoutSeconds = "300"
        # Piped into bash via a quoted heredoc, so nothing is written to
        # disk in dry-run mode and the script's $-expansions stay literal.
        runCommand = concat(
          ["bash -s -- '{{ mode }}' <<'GLASSBOX_ZRAM_SWAP'"],
          split("\n", trimspace(file("${path.module}/zram-swap.sh"))),
          ["GLASSBOX_ZRAM_SWAP"],
        )
      }
    }]
  })
}

resource "aws_ssm_association" "zram_swap" {
  association_name = "glassbox-zram-swap"
  name             = aws_ssm_document.zram_swap.name
  # Tracks the document's default version, so a script change updates the
  # association, and SSM re-runs it immediately on update.
  document_version    = aws_ssm_document.zram_swap.default_version
  schedule_expression = "cron(0 4 ? * SUN *)"

  parameters = {
    mode = "apply"
  }

  targets {
    key    = "InstanceIds"
    values = [aws_instance.glassbox.id]
  }
}
