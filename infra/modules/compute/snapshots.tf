# Daily EBS snapshots of the node, kept for 7 days (Data Lifecycle Manager).
#
# The node has one EBS volume, the 20 GB root volume (root_block_device in
# main.tf, no ebs_block_device). It holds everything stateful: the k3s
# datastore (SQLite under /var/lib/rancher/k3s/server/db) and both local-path
# PersistentVolumes (/var/lib/rancher/k3s/storage: MySQL, Redis). One
# snapshot of it is the whole server.
#
# The policy targets the INSTANCE (by its Name tag), not the volume: a
# restore ("Ops · Restore from snapshot", EC2 replace root volume) gives the
# instance a new root volume with a new ID, and an instance-targeted policy
# keeps snapshotting whatever root volume the instance has. It would also
# include any data volume added later, as one multi-volume snapshot set.
#
# Consistency: the snapshots are crash-consistent, the same as the disk after
# a power cut at that instant. MySQL's InnoDB (redo log, doublewrite buffer)
# and k3s's SQLite (WAL) recover from that on startup by design; Redis loads
# its last RDB/AOF. A DLM pre-script (an SSM document to `sync` or freeze the
# filesystem first) was considered and left out: it needs a second role with
# SSM permissions and an SSM document for little gain, since `sync` only
# narrows the window of unflushed writes, it doesn't make the databases more
# consistent than their own crash recovery does.
#
# Snapshot cost: incremental, about $0.05 per GB-month of changed blocks. The
# first is the used part of the disk; the daily ones are small.

data "aws_iam_policy_document" "dlm_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

# DLM's execution role. AWS's managed policy for it allows creating, tagging
# and deleting snapshots (only the ones its policies manage are deleted by
# DLM), and describing instances and volumes.
resource "aws_iam_role" "dlm" {
  name               = "glassbox-dlm"
  assume_role_policy = data.aws_iam_policy_document.dlm_trust.json
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "daily_root" {
  # DLM descriptions allow only letters, digits, spaces, _ and -.
  description        = "glassbox node daily snapshots kept 7 days"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    policy_type    = "EBS_SNAPSHOT_MANAGEMENT"
    resource_types = ["INSTANCE"]
    # DLM matches a resource that has ANY of the target tags, so only the
    # specific one: Name = glassbox (a rehearsal or other project instance
    # would carry a different Name).
    target_tags = { Name = "glassbox" }

    parameters {
      exclude_boot_volume = false
    }

    schedule {
      name = "daily-7"

      # DLM starts within the hour after this time. 04:00 UTC is the
      # quietest hour for the site, and clear of the 2-hourly warm-up at :17.
      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["04:00"]
      }

      retain_rule {
        count = 7
      }

      # The restore runbook (and the glassbox-ops role, by tag condition)
      # accepts only snapshots with these tags.
      tags_to_add = {
        Name            = "glassbox-daily"
        project         = "glassbox"
        glassbox-backup = "daily-root"
      }

      variable_tags = {
        instance-id = "$(instance-id)"
      }

      copy_tags = false
    }
  }

  tags = { Name = "glassbox-daily-snapshots" }

  depends_on = [aws_iam_role_policy_attachment.dlm]
}
