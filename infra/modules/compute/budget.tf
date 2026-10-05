# Two monthly budgets and the automatic Bedrock answer stop (owner decision
# 2026-10-05: the node's fixed costs, about $17.50/month on demand once the
# EC2 trial ends, would trip a whole-bill stop for reasons that have nothing to
# do with LLM spend, so the stop watches Bedrock alone).
#
#   glassbox-monthly-cost      Whole account, $var.monthly_budget_usd (30) a
#                              month, unblended, gross: credits and refunds are
#                              left out, so promotional credits can't hide real
#                              usage (the older hand-made budget counted credits
#                              and read $0). Email alerts only, at actual 50%,
#                              80% and 100% and at forecasted 100%. No action.
#   glassbox-bedrock-answers   Bedrock only, $var.bedrock_budget_usd (12.50) a
#                              month, unblended, gross. Counts first-party
#                              Bedrock (SERVICE "Amazon Bedrock") and anything
#                              billed through AWS Marketplace (BILLING_ENTITY
#                              "AWS Marketplace"), which is how third-party
#                              Bedrock models such as Anthropic's or a later
#                              OpenAI GPT-6 Luna offer are billed; see the
#                              filter below. Emails at actual 80% and 100%.
#   answer-model stop          At actual 100% of the Bedrock budget AWS Budgets
#                              itself (approval model AUTOMATIC) attaches
#                              glassbox-budget-stop-answer-models to the node's
#                              role. It denies the answer models only (every
#                              bedrock_profiles entry: their inference profiles
#                              and the foundation models behind them), never the
#                              Titan embedding model, so retrieval keeps working
#                              and the site answers in retrieval-only mode
#                              (services/glassbox/api/ask.py,
#                              LLMAccessDeniedError).
#   glassbox-budget-action     The role Budgets assumes to run the action. It can
#                              only attach and detach that one policy on that
#                              one role.
#
# Lifting the stop: AWS Budgets resets IAM-policy actions at the start of each
# budget period (the 1st of the month, UTC), detaching the policy; to lift it
# earlier, reverse the action in the console. See infra/CI.md "Budget stop".
# Billing data is updated at least once a day and lags by hours, so Bedrock
# spend can pass the limit before the stop fires.

locals {
  account_budget_name = "glassbox-monthly-cost"
  bedrock_budget_name = "glassbox-bedrock-answers"

  # Built from bedrock_profiles (main.tf), so a model added there (for example
  # a later GPT-6 Luna profile) is covered by the stop in the same change.
  answer_model_deny_resources = concat(
    [
      for profile in values(local.bedrock_profiles) :
      "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:inference-profile/${profile.profile_id}"
    ],
    flatten([
      for profile in values(local.bedrock_profiles) : [
        for region in local.bedrock_destination_regions :
        "arn:aws:bedrock:${region}::foundation-model/${profile.model_id}"
      ]
    ]),
  )

  # AWS's confused-deputy guidance for Budgets uses budget/* as the source ARN;
  # the exact form Budgets sends when it runs an action is not documented, so a
  # narrower pattern could stop the action from ever assuming this role.
  # aws:SourceAccount still limits it to this account's budgets.
  budget_source_arns = [
    "arn:aws:budgets::${var.aws_account_id}:budget/*",
  ]
}

# ---- The deny policy the action attaches ------------------------------------

# Converse and ConverseStream have no IAM actions of their own: they are
# authorized as InvokeModel and InvokeModelWithResponseStream (service
# authorization reference, Amazon Bedrock).
data "aws_iam_policy_document" "answer_model_stop" {
  statement {
    sid       = "BudgetStopAnswerModels"
    effect    = "Deny"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = local.answer_model_deny_resources
  }
}

resource "aws_iam_policy" "answer_model_stop" {
  name        = "glassbox-budget-stop-answer-models"
  description = "Attached by the glassbox-monthly-cost budget action at 100% of the monthly budget: denies the Bedrock answer models, not Titan embeddings."
  policy      = data.aws_iam_policy_document.answer_model_stop.json

  lifecycle {
    # Retrieval needs Titan: the stop must never cover the embedding model.
    precondition {
      condition     = alltrue([for arn in local.answer_model_deny_resources : !strcontains(arn, "titan")])
      error_message = "The budget stop policy must not deny the Titan embedding model."
    }
  }
}

# ---- The role AWS Budgets assumes to run the action -------------------------

data "aws_iam_policy_document" "budget_action_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
    # Confused-deputy protection: only this account's glassbox-monthly-cost
    # budget (or one of its actions) may make Budgets assume the role.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = local.budget_source_arns
    }
  }
}

resource "aws_iam_role" "budget_action" {
  name               = "glassbox-budget-action"
  description        = "Assumed by AWS Budgets to attach or detach the answer-model stop policy on glassbox-instance."
  assume_role_policy = data.aws_iam_policy_document.budget_action_trust.json
}

data "aws_iam_policy_document" "budget_action" {
  statement {
    sid       = "AttachOrDetachTheStopOnTheNodeRoleOnly"
    actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = [aws_iam_role.instance.arn]
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = [aws_iam_policy.answer_model_stop.arn]
    }
  }
}

resource "aws_iam_role_policy" "budget_action" {
  name   = "glassbox-budget-action"
  role   = aws_iam_role.budget_action.id
  policy = data.aws_iam_policy_document.budget_action.json
}

# ---- Budget, alerts and the action ------------------------------------------

resource "aws_budgets_budget" "monthly" {
  name         = local.account_budget_name
  budget_type  = "COST"
  limit_amount = format("%.2f", var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    use_blended    = false
    use_amortized  = false
    include_credit = false
    include_refund = false
  }

  dynamic "notification" {
    for_each = [
      { type = "ACTUAL", threshold = 50 },
      { type = "ACTUAL", threshold = 80 },
      { type = "ACTUAL", threshold = 100 },
      { type = "FORECASTED", threshold = 100 },
    ]
    content {
      notification_type          = notification.value.type
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}

# Bedrock spend, first party and Marketplace. Third-party Bedrock models are
# sold through AWS Marketplace: they bill under their own product name (for
# example "Claude ... (Amazon Bedrock Edition)") with billing entity "AWS
# Marketplace", not under SERVICE "Amazon Bedrock". The product name of a
# model not used yet (GPT-6 Luna) can't be known before its first charge, so
# the filter takes the documented, stable dimension instead: BILLING_ENTITY
# "AWS Marketplace" (Cost Explorer: "AWS Marketplace: Identifies a purchase
# in AWS Marketplace"). Trade-off: any other Marketplace purchase in this
# account would also count toward the Bedrock budget and could fire the stop
# early. Today the account has none.
#
# filter_expression needs metrics and can't be combined with cost_types, so
# "gross" is expressed in the filter: RECORD_TYPE Credit and Refund are left
# out, like include_credit/include_refund = false on the account budget.
resource "aws_budgets_budget" "bedrock" {
  name         = local.bedrock_budget_name
  budget_type  = "COST"
  limit_amount = format("%.2f", var.bedrock_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  metrics      = ["UnblendedCost"]

  filter_expression {
    and {
      or {
        dimensions {
          key    = "SERVICE"
          values = ["Amazon Bedrock"]
        }
      }
      or {
        dimensions {
          key    = "BILLING_ENTITY"
          values = ["AWS Marketplace"]
        }
      }
    }
    and {
      not {
        dimensions {
          key    = "RECORD_TYPE"
          values = ["Credit", "Refund"]
        }
      }
    }
  }

  # actual 100% matches the action's threshold exactly (same operator and
  # subscriber): the provider reads every notification on the budget back, so a
  # notification the action shares must also be in this list or every plan
  # would try to delete it.
  dynamic "notification" {
    for_each = [80, 100]
    content {
      notification_type          = "ACTUAL"
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = [var.alert_email]
    }
  }

  lifecycle {
    precondition {
      condition     = var.bedrock_budget_usd > 0 && var.bedrock_budget_usd < var.monthly_budget_usd
      error_message = "The Bedrock budget must be positive and below the whole-account budget."
    }
  }
}

resource "aws_budgets_budget_action" "answer_model_stop" {
  budget_name        = aws_budgets_budget.bedrock.name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action.arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 100
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.answer_model_stop.arn
      roles      = [aws_iam_role.instance.name]
    }
  }

  subscriber {
    subscription_type = "EMAIL"
    address           = var.alert_email
  }

  # The role must be able to act before Budgets first evaluates the action.
  depends_on = [aws_iam_role_policy.budget_action]
}
