# parser_service.py

class ParserService:
    @staticmethod
    def parse_place_info(text):
        data = {}
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        for line in lines:
            if ':' not in line:
                continue
            key_part, value_part = line.split(':', 1)
            key = key_part.strip().lower()
            value = value_part.strip()

            if key == 'название':
                data['name'] = value
            elif key == 'описание':
                data['description'] = value
            elif key == 'ключ':
                data['key'] = value

        if all(k in data for k in ['name', 'description', 'key']):
            return data
        return None