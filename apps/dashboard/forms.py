from __future__ import annotations

from django import forms


class APIKeyCreateForm(forms.Form):
    label = forms.CharField(max_length=120, required=False)
    rate_limit_rpm = forms.IntegerField(min_value=1, max_value=1_000_000, initial=120)
