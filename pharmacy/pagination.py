# your_app/pagination.py
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

class CustomPagination(PageNumberPagination):
    page_size_query_param = 'page_size'
    page_query_param = 'pageNumber'
    max_page_size = 100

    def get_paginated_response(self, data):
        # Handle missing params gracefully
        page_number = self.request.query_params.get(self.page_query_param, 1)
        page_size = self.request.query_params.get(self.page_size_query_param, self.page.paginator.per_page)

        return Response({
            "results": data,
            "pagination": {
                "pageNumber": int(page_number),
                "pageSize": int(page_size),
                "totalItems": self.page.paginator.count,
                "totalPages": self.page.paginator.num_pages,
                "hasNextPage": self.page.has_next(),
                "hasPreviousPage": self.page.has_previous(),
            }
        })
