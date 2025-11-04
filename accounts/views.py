from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .serializers import UserSerializer  # adjust path if needed

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_current_user(request):
    """
    Returns the authenticated user's data.
    """
    serializer = UserSerializer(request.user)
    return Response(serializer.data)