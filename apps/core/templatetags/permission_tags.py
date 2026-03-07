from django import template

from apps.core.permissions import has_action_permission, has_permission


register = template.Library()


@register.filter(name='has_action_perm')
def has_action_perm(user, action_code):
    """Usage: {% if request.user|has_action_perm:'transfer.create_task' %}"""
    return has_action_permission(user, action_code)


@register.filter(name='has_module_perm')
def has_module_perm(user, perm_code):
    """
    Usage: {% if request.user|has_module_perm:'orders:delete' %}
    Fallback action is 'view' when action part omitted.
    """
    if not perm_code:
        return False
    text = str(perm_code)
    if ':' in text:
        module, action = text.split(':', 1)
    else:
        module, action = text, 'view'
    module = (module or '').strip()
    action = (action or 'view').strip()
    if not module:
        return False
    return has_permission(user, module, action)
