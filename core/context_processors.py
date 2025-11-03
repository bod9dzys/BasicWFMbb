from django.db.models import Q
from .forms import SickLeaveProofUploadForm


def sick_leave_notifications(request):
    pending_entries = []
    if request.user.is_authenticated and hasattr(request.user, "agent"):
        pending_filter = Q(attachment__isnull=True) | Q(attachment="")
        pending_qs = request.user.agent.sick_leave_proofs.filter(pending_filter).select_related("agent").order_by("-created_at")
        for proof in pending_qs:
            pending_entries.append(
                {
                    "proof": proof,
                    "form": SickLeaveProofUploadForm(
                        instance=proof,
                        auto_id=f"id_nav_attachment_{proof.pk}_%s",
                    ),
                }
            )
    return {
        "has_pending_sick_leave_proof": bool(pending_entries),
        "pending_sick_leave_proof_count": len(pending_entries),
        "pending_sick_leave_proofs": pending_entries,
    }
