from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

def custom_exception_handler(exc, context):
    # Get default error response
    response = exception_handler(exc, context)

    if response is not None:
        # Normalize DRF default errors
        detail = response.data.get('detail', None)
        message = detail if detail else response.data
        response.data = {
            'error': True,
            'message': message,
            'status_code': response.status_code
        }
        return response

    # Handle other unhandled exceptions (e.g., ValueError)
    return Response({
        'error': True,
        'message': str(exc) or "An unexpected error occurred.",
        'status_code': status.HTTP_500_INTERNAL_SERVER_ERROR
    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
