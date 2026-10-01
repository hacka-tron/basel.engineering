output "document_names" {
  description = "Runbook action => SSM Command document name."
  value       = { for key, doc in aws_ssm_document.ops : key => doc.name }
}
