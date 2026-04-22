from rest_framework import serializers
from content_app.models import Video


class VideoSerializer(serializers.ModelSerializer):
    thumbnail_url = serializers.SerializerMethodField()

    def get_thumbnail_url(self, obj):
        request = self.context.get("request")
        thumbnail_path = obj.thumbnail_url or "/media/thumbnail/dummy.jpg"
        if request and thumbnail_path.startswith("/"):
            return request.build_absolute_uri(thumbnail_path)
        return thumbnail_path

    class Meta:
        model = Video
        fields = [
            'id',
            'created_at',
            'title',
            'description',
            'thumbnail_url',
            'category',
        ]
