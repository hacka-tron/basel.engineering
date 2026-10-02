# Vetted runbook actions for the single k3s node, as SSM Command documents.
#
# .github/workflows/ops.yml can only run these documents (plus the
# compute module's glassbox-zram-swap): the glassbox-ops / glassbox-ops-read
# roles in infra/bootstrap may call ssm:SendCommand with glassbox-ops-*
# documents only, never AWS-RunShellScript. So the code that runs as root on
# the node is exactly what was reviewed here and applied by Terraform, and
# the only inputs are enum parameters that SSM validates against
# allowedValues before anything runs (the scripts re-check them).
#
# Each document = scripts/lib.sh + one action script, piped into `bash -s`
# through a quoted heredoc (same pattern as the compute module's zram
# document), with the parameters as positional arguments. Output stays in
# SSM Run Command history; the workflow prints it.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66.0"
    }
  }
}

locals {
  heredoc_marker = "GLASSBOX_OPS"
  lib            = trimspace(file("${path.module}/scripts/lib.sh"))

  flux_targets = ["keda", "keda-scaling", "ingest", "app-ready", "flux-system", "helmrelease-keda"]

  # name suffix => script, fixed leading arguments, enum parameters (in
  # argument order), and the on-node execution timeout. Changing an
  # allowed value here also needs the matching choice in ops.yml.
  documents = {
    diagnose = {
      description = "Read-only snapshot: node memory/PSI/swap, pods, events, Flux, recent k3s errors, CSP report counts, LLM budget counters and query-log counts. Changes nothing."
      script      = "diagnose.sh"
      fixed_args  = []
      parameters  = []
      timeout     = 300
    }
    boot-id = {
      description = "Read-only: print this boot's ID and boot time. reboot-node uses it to prove the node really rebooted."
      script      = "boot-id.sh"
      fixed_args  = []
      parameters  = []
      timeout     = 60
    }
    restart-deployment = {
      description = "kubectl rollout restart of one known Deployment, then wait for the rollout."
      script      = "restart-deployment.sh"
      fixed_args  = []
      parameters = [{
        name        = "deployment"
        description = "Deployment to restart."
        values      = ["api", "retrieval-worker", "traefik"]
      }]
      timeout = 900
    }
    flux-suspend = {
      description = "Suspend one known Flux Kustomization or the keda HelmRelease (like flux suspend)."
      script      = "flux-suspend-resume.sh"
      fixed_args  = ["suspend"]
      parameters = [{
        name        = "target"
        description = "Flux object to suspend."
        values      = local.flux_targets
      }]
      timeout = 300
    }
    flux-resume = {
      description = "Resume one known Flux Kustomization or the keda HelmRelease and reconcile it (like flux resume)."
      script      = "flux-suspend-resume.sh"
      fixed_args  = ["resume"]
      parameters = [{
        name        = "target"
        description = "Flux object to resume."
        values      = local.flux_targets
      }]
      timeout = 600
    }
    flux-reconcile = {
      description = "Fetch the deploy branch and reconcile Flux now (like flux reconcile ks flux-system --with-source)."
      script      = "flux-reconcile.sh"
      fixed_args  = []
      parameters  = []
      timeout     = 900
    }
    scale-keda = {
      description = "Scale every KEDA Deployment in the keda namespace to 0 or 1 replicas."
      script      = "scale-keda.sh"
      fixed_args  = []
      parameters = [{
        name        = "replicas"
        description = "Replica count for each KEDA Deployment."
        values      = ["0", "1"]
      }]
      timeout = 600
    }
    reindex = {
      description = "Run python -m services.glassbox.ingest.run --reindex once as a Job (the api's current image): rewrite every Redis chunk key from MySQL. Refuses mid-release."
      script      = "reindex.sh"
      fixed_args  = []
      parameters  = []
      timeout     = 1800
    }
    cronjob-suspend = {
      description = "Suspend one known CronJob in the app namespace."
      script      = "cronjob-suspend-resume.sh"
      fixed_args  = ["suspend"]
      parameters = [{
        name        = "cronjob"
        description = "CronJob to suspend."
        values      = ["warm-answers"]
      }]
      timeout = 300
    }
    cronjob-resume = {
      description = "Resume one known CronJob in the app namespace."
      script      = "cronjob-suspend-resume.sh"
      fixed_args  = ["resume"]
      parameters = [{
        name        = "cronjob"
        description = "CronJob to resume."
        values      = ["warm-answers"]
      }]
      timeout = 300
    }
  }

  rendered = {
    for key, doc in local.documents : key => {
      body = "${local.lib}\n\n${trimspace(file("${path.module}/scripts/${doc.script}"))}"
      args = join(" ", concat(
        [for arg in doc.fixed_args : "'${arg}'"],
        [for param in doc.parameters : "'{{ ${param.name} }}'"],
      ))
    }
  }
}

resource "aws_ssm_document" "ops" {
  for_each = local.documents

  name            = "glassbox-ops-${each.key}"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"

  # Same explicit tag as the compute module's zram document; the CI role's
  # SSM permissions are scoped to glassbox-* documents.
  tags = { project = "glassbox" }

  content = jsonencode({
    schemaVersion = "2.2"
    description   = each.value.description
    parameters = {
      for param in each.value.parameters : param.name => {
        type           = "String"
        description    = param.description
        allowedValues  = param.values
        allowedPattern = "^(${join("|", param.values)})$"
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = replace(each.key, "-", "")
      precondition = {
        StringEquals = ["platformType", "Linux"]
      }
      inputs = {
        timeoutSeconds = tostring(each.value.timeout)
        runCommand = concat(
          ["bash -s -- ${local.rendered[each.key].args} <<'${local.heredoc_marker}'"],
          split("\n", local.rendered[each.key].body),
          [local.heredoc_marker],
        )
      }
    }]
  })

  lifecycle {
    precondition {
      condition = alltrue([
        !strcontains(local.rendered[each.key].body, "{{"),
        !strcontains(local.rendered[each.key].body, "}}"),
      ])
      error_message = "Ops scripts must not contain double curly braces: SSM would read them as document parameters."
    }
    precondition {
      condition     = !contains(split("\n", local.rendered[each.key].body), local.heredoc_marker)
      error_message = "Ops scripts must not contain a line equal to the heredoc marker."
    }
  }
}
