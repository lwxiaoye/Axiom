import unittest

from app.services.platform.user_display_name import pick_display_name


class UserDisplayNameTest(unittest.TestCase):
    def test_pick_display_name_prefers_realname_then_username_then_user_id(self):
        self.assertEqual(pick_display_name("u1", {"realname": "张三", "username": "zhangsan"}), "张三")
        self.assertEqual(pick_display_name("u2", {"realname": " ", "username": "lisi"}), "lisi")
        self.assertEqual(pick_display_name("u3", {}), "u3")


if __name__ == "__main__":
    unittest.main()
