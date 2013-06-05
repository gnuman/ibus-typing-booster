# -*- coding: utf-8 -*-
# vim:et sts=4 sw=4
#
# ibus-typing-booster - The Tables engine for IBus
#
# Copyright (c) 2011-2013 Anish Patil <apatil@redhat.com>
# Copyright (c) 2012-2013 Mike FABIAN <mfabian@redhat.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#  This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#  You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>

import os
import os.path as path
import sys
import unicodedata
import sqlite3
import uuid
import time
import re
import hunspell_suggest

user_database_version = '0.63'

class ImeProperties:
    def __init__(self, configfile_path=None):
        '''
        configfile_path is the full path to the config file, for example
        “/usr/share/ibus-typing-booster/hunspell-tables/en_US.conf”
        '''
        self.ime_property_cache = {}
        if configfile_path.find('typing-booster:') > 0:
            configfile_path=configfile_path.replace(
                'typing-booster:','')
        if os.path.exists(configfile_path) and os.path.isfile(configfile_path):
            comment_patt = re.compile('^#')
            for line in file(configfile_path):
                if not comment_patt.match(line):
                    attr,val = line.strip().split ('=', 1)
                    self.ime_property_cache[attr.strip()]= val.strip().decode('utf-8')
        else:
            sys.stderr.write("Error: ImeProperties: No such file: %s" %configfile_path)

    def get(self, key):
        if key in self.ime_property_cache:
            return self.ime_property_cache[key]
        else:
            return None

class tabsqlitedb:
    '''Phrase databases for ibus-typing-booster

    The phrases table in the database has columns with the names:

    “id”, “input_phrase”, “phrase”, “user_freq”

    There are 2 databases, sysdb, userdb.

    sysdb: “Database” with the suggestions from the hunspell dictionaries
        user_freq = 0 always.

        Actually there is no Sqlite3 database called “sysdb”, these
        are the suggestions coming from hunspell_suggest, i.e. from
        grepping the hunspell dictionaries and from pyhunspell.
        (Historic note: ibus-typing-booster started as a fork of
        ibus-table, in ibus-table “sysdb” is a Sqlite3 database
        which is installed systemwide and readonly for the user)

    user_db: Database on disk where the phrases learned from the user are stored
        user_freq >= 1: The number of times the user has used this phrase
    '''
    def __init__(self, name = 'table.db', user_db = None, filename = None ):
        # use filename when you are creating db from source
        # use name when you are using db
        self._phrase_table_column_names = ['id', 'input_phrase', 'phrase', 'user_freq']

        self.old_phrases=[]

        self._conf_file_path = "/usr/share/ibus-typing-booster/hunspell-tables/"

        self.ime_properties = ImeProperties(self._conf_file_path+filename)
        self._language = self.ime_properties.get('language')
        self._normalization_form_internal = 'NFD'
        if self._language.startswith('ko'):
            self._normalization_form_internal = 'NFKD'
        self._m17ndb = 'm17n'
        self._m17n_mim_name = ""

        self.hunspell_obj = hunspell_suggest.Hunspell(
            lang=self._language,
            dict_name=self.ime_properties.get("hunspell_dict"),
            aff_name=self.ime_properties.get("hunspell_dict").replace('.dic', '.aff'))

        user_db = self.ime_properties.get("name")+'-user.db'
        # user database:
        if user_db != None:
            home_path = os.getenv ("HOME")
            tables_path = path.join (home_path, ".local/share/.ibus",  "hunspell-tables")
            if not path.isdir (tables_path):
                os.makedirs (tables_path)
            user_db = path.join (tables_path, user_db)
            if not path.exists(user_db):
                sys.stderr.write("The user database %(udb)s does not exist yet.\n" %{'udb': user_db})
            else:
                try:
                    desc = self.get_database_desc (user_db)
                    if desc == None \
                        or desc["version"] != user_database_version \
                        or self.get_number_of_columns_of_phrase_table(user_db) != len(self._phrase_table_column_names):
                        sys.stderr.write("The user database %(udb)s seems to be incompatible.\n" %{'udb': user_db})
                        if desc == None:
                            sys.stderr.write("There is no version information in the database.\n")
                        elif desc["version"] != user_database_version:
                            sys.stderr.write("The version of the database does not match (too old or too new?).\n")
                            sys.stderr.write("ibus-typing-booster wants version=%s\n" %user_database_version)
                            sys.stderr.write("But the  database actually has version=%s\n" %desc["version"])
                        elif self.get_number_of_columns_of_phrase_table(user_db) != len(self._phrase_table_column_names):
                            sys.stderr.write("The number of columns of the database does not match.\n")
                            sys.stderr.write("ibus-typing-booster expects %(col)s columns.\n"
                                %{'col': len(self._phrase_table_column_names)})
                            sys.stderr.write("But the database actually has %(col)s columns.\n"
                                %{'col': self.get_number_of_columns_of_phrase_table(user_db)})
                        sys.stderr.write("Trying to recover the phrases from the old, incompatible database.\n")
                        self.old_phrases = self.extract_user_phrases( user_db )
                        from time import strftime
                        new_name = "%(basename)s.%(time)s" %{'basename': user_db, 'time': strftime('%Y-%m-%d_%H:%M:%S')}
                        sys.stderr.write("Renaming the incompatible database to \"%(name)s\".\n" %{'name': new_name})
                        os.rename(user_db, new_name)
                        sys.stderr.write("Creating a new, empty database \"%(name)s\".\n"  %{'name': user_db})
                        self.init_user_db(user_db)
                        sys.stderr.write("If user phrases were successfully recovered from the old,\n")
                        sys.stderr.write("incompatible database, they will be used to initialize the new database.\n")
                    else:
                        sys.stderr.write("Compatible database %(db)s found.\n" %{'db': user_db})
                except:
                    import traceback
                    traceback.print_exc()
        else:
            user_db = ":memory:"

        # open user phrase database
        try:
            sys.stderr.write("Connect to the database %(name)s.\n" %{'name': user_db})
            self.db = sqlite3.connect(user_db)
            self.db.execute('PRAGMA encoding = "UTF-8";')
            self.db.execute('PRAGMA case_sensitive_like = true;')
            self.db.execute('PRAGMA page_size = 4096; ')
            self.db.execute('PRAGMA cache_size = 20000;')
            self.db.execute('PRAGMA temp_store = MEMORY;')
            self.db.execute('PRAGMA journal_mode = WAL;')
            self.db.execute('PRAGMA synchronous = NORMAL;')
            self.db.execute('ATTACH DATABASE "%s" AS user_db;' % user_db)
        except:
            sys.stderr.write("Could not open the database %(name)s.\n" %{'name': user_db})
            from time import strftime
            new_name = "%(basename)s.%(time)s" %{'basename': user_db, 'time': strftime('%Y-%m-%d_%H:%M:%S')}
            sys.stderr.write("Renaming the incompatible database to \"%(name)s\".\n" %{'name': new_name})
            os.rename(user_db, new_name)
            sys.stderr.write("Creating a new, empty database \"%(name)s\".\n"  %{'name': user_db})
            self.init_user_db(user_db)
            self.db.execute('ATTACH DATABASE "%s" AS user_db;' % user_db)
        self.create_tables("user_db")
        if self.old_phrases:
            # (phrase, user_freq)
            map(lambda x: self.add_phrase(
                input_phrase=x[0], phrase=x[0], user_freq=x[1],
                database = 'user_db', commit = False),
                self.old_phrases)
            self.db.commit()

        # do not call this always on intialization for the moment.
        # It makes the already slow “python engine/main.py --xml”
        # to list the engines even slower and may break the listing
        # of the engines completely if there is a problem with
        # optimizing the databases. Probably bring this back as an
        # option later if the code in self.optimize_database() is
        # improved to do anything useful.
        #try:
        #    self.optimize_database()
        #except:
        #    print "exception in optimize_database()"
        #    import traceback
        #    traceback.print_exc ()

        # try create all hunspell-tables in user database
        self.create_indexes ("user_db",commit=False)
        self.generate_userdb_desc ()

    def update_phrase (self, input_phrase=u'', phrase=u'', user_freq=0, database='user_db', commit=True):
        '''
        update the user frequency of a phrase
        '''
        if not input_phrase or not phrase:
            return
        input_phrase = unicodedata.normalize(
            self._normalization_form_internal, input_phrase)
        phrase = unicodedata.normalize(
            self._normalization_form_internal, phrase)
        sqlstr = '''
        UPDATE %(database)s.phrases
        SET user_freq = :user_freq
        WHERE input_phrase = :input_phrase
        AND phrase = :phrase
        ;''' %{'database':database}
        sqlargs = {'user_freq': user_freq,
                   'input_phrase': input_phrase,
                   'phrase': phrase}
        self.db.execute(sqlstr, sqlargs)
        if commit:
            self.db.commit()

    def sync_usrdb (self):
        '''
        Trigger a checkpoint operation.
        '''
        self.db.commit()
        self.db.execute('PRAGMA wal_checkpoint;')

    def create_tables (self, database):
        '''Create table for the phrases.'''
        sqlstr = '''CREATE TABLE IF NOT EXISTS %s.phrases
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    input_phrase TEXT, phrase TEXT,
                    user_freq INTEGER);''' % database
        self.db.execute(sqlstr)
        self.db.commit()

    def add_phrase (self, input_phrase=u'', phrase=u'', user_freq=0, database = 'main', commit=True):
        '''
        Add phrase to database
        '''
        if not input_phrase or not phrase:
            return
        input_phrase = unicodedata.normalize(
            self._normalization_form_internal, input_phrase)
        phrase = unicodedata.normalize(
            self._normalization_form_internal, phrase)
        select_sqlstr= '''
        SELECT * FROM %(database)s.phrases
        WHERE input_phrase = :input_phrase AND phrase = :phrase
        ;'''  %{'database': database}
        select_sqlargs = {'input_phrase': input_phrase, 'phrase': phrase}
        if self.db.execute(select_sqlstr, select_sqlargs).fetchall():
            # there is already such a phrase, i.e. add_phrase was called
            # in error, do nothing to avoid duplicate entries.
            return

        insert_sqlstr = '''
        INSERT INTO %(database)s.phrases
        (input_phrase, phrase, user_freq)
        VALUES (:input_phrase, :phrase, :user_freq)
        ;''' %{'database': database}
        insert_sqlargs = {'input_phrase': input_phrase, 'phrase': phrase,
                          'user_freq': user_freq}
        try:
            self.db.execute (insert_sqlstr, insert_sqlargs)
            if commit:
                self.db.commit()
        except Exception:
            import traceback
            traceback.print_exc()

    def optimize_database (self, database='main'):
        sqlstr = '''
            CREATE TABLE tmp AS SELECT * FROM %(database)s.phrases;
            DELETE FROM %(database)s.phrases;
            INSERT INTO %(database)s.phrases SELECT * FROM tmp ORDER BY
            input_phrase, user_freq DESC, id ASC;
            DROP TABLE tmp;''' %{'database':database,}
        self.db.executescript (sqlstr)
        self.db.executescript ("VACUUM;")
        self.db.commit()

    def drop_indexes(self, database):
        '''Drop the index in database to reduce it's size'''
        sqlstr = '''
            DROP INDEX IF EXISTS %(database)s.phrases_index_p;
            DROP INDEX IF EXISTS %(database)s.phrases_index_i;
            VACUUM;
            ''' % { 'database':database }

        self.db.executescript (sqlstr)
        self.db.commit()

    def create_indexes(self, database, commit=True):
        sqlstr = '''
        CREATE INDEX IF NOT EXISTS %(database)s.phrases_index_p ON phrases
        (input_phrase, id ASC);
        CREATE INDEX IF NOT EXISTS %(database)s.phrases_index_i ON phrases
        (phrase)
        ;''' %{'database':database}
        self.db.executescript (sqlstr)
        if commit:
            self.db.commit()

    def select_words(self, input_phrase):
        '''
        Get phrases from database completing input_phrase.

        Returns a list of matches where each match is a tuple in the
        form of (phrase, user_freq), i.e. returns something like
        [(phrase, user_freq), ...]
        '''
        if type(input_phrase) != type(u''):
            input_phrase = input_phrase.decode('utf8')
        input_phrase = unicodedata.normalize(
            self._normalization_form_internal, input_phrase)
        # Get (phrase, user_freq) pairs from user_db.
        #
        # Example: Let’s assume the user typed “co” and user_db contains
        #
        #     1|colou|colour|1
        #     2|col|colour|2
        #     3|co|colour|1
        #     4|co|cold|1
        #     5|conspirac|conspiracy|5
        #     6|conspi|conspiracy|1
        #     7|c|conspiracy|1
        #
        # Then the result returned by .fetchall() is:
        #
        # [(u'colour', 4), (u'cold', 1), (u'conspiracy', 6)]
        #
        # (“c|conspiracy|1” is not selected because it doesn’t
        # match the user input “LIKE co%”!)
        sqlstr = '''
        SELECT phrase, sum(user_freq)
        FROM user_db.phrases WHERE input_phrase LIKE :input_phrase
        GROUP BY phrase
        limit 1000
        ;'''
        sqlargs = {'input_phrase': input_phrase+'%'}
        try:
            results = self.db.execute(sqlstr, sqlargs).fetchall()
        except:
            import traceback
            traceback.print_exc()
        phrase_frequencies = {}
        map(lambda x: phrase_frequencies.update([(x, 0)]), self.hunspell_obj.suggest(input_phrase))
        # Now phrase_frequencies might contain something like this:
        #
        # {u'code': 0, u'communicability': 0, u'cold': 0, u'colour': 0}
        #
        # Updating this dictionary filled only with hunspell data
        # so far with the results of the SELECT statement, i.e.
        #
        # [(u'colour', 4), (u'cold', 1), (u'conspiracy', 6)]
        #
        # then gives us
        #
        # {u'conspiracy': 6, u'code': 0, u'communicability': 0, u'cold': 1, u'colour': 4}
        phrase_frequencies.update(results)

        candidates = []
        for phrase, user_freq in sorted(phrase_frequencies.items(),
                                        key=lambda x: (
                                            -1*x[1],   # user_freq descending
                                            len(x[0]), # len(phrase) ascending
                                            x[0]       # phrase alphabetical
                                        )):
            candidates.append((phrase, user_freq))
        return candidates[:]

    def generate_userdb_desc (self):
        try:
            sqlstring = 'CREATE TABLE IF NOT EXISTS user_db.desc (name PRIMARY KEY, value);'
            self.db.executescript (sqlstring)
            sqlstring = 'INSERT OR IGNORE INTO user_db.desc  VALUES (?, ?);'
            self.db.execute (sqlstring, ('version', user_database_version))
            sqlstring = 'INSERT OR IGNORE INTO user_db.desc  VALUES (?, DATETIME("now", "localtime"));'
            self.db.execute (sqlstring, ("create-time", ))
            self.db.commit ()
        except:
            import traceback
            traceback.print_exc ()

    def init_user_db (self,db_file):
        if not path.exists (db_file):
            db = sqlite3.connect (db_file)
            db.execute('PRAGMA encoding = "UTF-8";')
            db.execute('PRAGMA case_sensitive_like = true;')
            db.execute('PRAGMA page_size = 4096;')
            # a database containing the complete German Hunspell
            # dictionary has less then 6000 pages. 20000 pages
            # should be enough to cache the complete database
            # in most cases.
            db.execute('PRAGMA cache_size = 20000;')
            db.execute('PRAGMA temp_store = MEMORY; ')
            db.execute('PRAGMA journal_mode = WAL;')
            db.execute('PRAGMA synchronous = NORMAL;')
            db.commit()

    def get_database_desc(self, db_file):
        if not path.exists(db_file):
            return None
        try:
            db = sqlite3.connect(db_file)
            desc = {}
            for row in db.execute("SELECT * FROM desc;").fetchall():
                desc[row[0]] = row[1]
            db.close()
            return desc
        except:
            return None

    def get_number_of_columns_of_phrase_table(self, db_file):
        '''
        Get the number of columns in the 'phrases' table in
        the database in db_file.

        Determines the number of columns by parsing this:

        sqlite> select sql from sqlite_master where name='phrases';
CREATE TABLE phrases (id INTEGER PRIMARY KEY AUTOINCREMENT, input_phrase TEXT, phrase TEXT, user_freq INTEGER)
        sqlite>

        This result could be on a single line, as above, or on multiple
        lines.
        '''
        if not path.exists (db_file):
            return 0
        try:
            db = sqlite3.connect (db_file)
            tp_res = db.execute(
                "select sql from sqlite_master where name='phrases';"
            ).fetchall()
            # Remove possible line breaks from the string where we
            # want to match:
            str = ' '.join(tp_res[0][0].splitlines())
            res = re.match(r'.*\((.*)\)', str)
            if res:
                tp = res.group(1).split(',')
                return len(tp)
            else:
                return 0
        except:
            return 0

    def check_phrase_and_update_frequency(self, input_phrase=u'', phrase=u'', database='user_db'):
        '''
        Check whether input_phrase and phrase are already in database. If
        they are in the database, increase the frequency by 1, if not
        add them.
        '''
        if not input_phrase:
            input_phrase = phrase
        if not phrase:
            return
        if type(phrase) != type(u''):
            phrase = phrase.decode('utf8')
        if type(input_phrase) != type(u''):
            input_phrase = input_phrase.decode('utf8')
        phrase = unicodedata.normalize(
            self._normalization_form_internal, phrase)
        input_phrase = unicodedata.normalize(
            self._normalization_form_internal, input_phrase)

        # There should never be more than 1 database row for the same
        # input_phrase *and* phrase. So the following query on
        # the database should match at most one database
        # row and the length of the result array should be 0 or
        # 1. So the “GROUP BY phrase” is actually redundant. It is
        # only a safeguard for the case when duplicate rows have been
        # added to the database accidentally (But in that case there
        # is a bug somewhere else which should be fixed).
        sqlstr = '''
        SELECT max(user_freq) FROM user_db.phrases
        WHERE phrase = :phrase AND input_phrase = :input_phrase
        GROUP BY phrase
        ;'''
        sqlargs = {'phrase': phrase, 'input_phrase': input_phrase}
        result = self.db.execute(sqlstr, sqlargs).fetchall()
        if len(result) > 0:
            # A match was found in user_db, increase user frequency by 1
            self.update_phrase(input_phrase = input_phrase,
                               phrase = phrase,
                               user_freq = result[0][0]+1,
                               database='user_db', commit=True);
            return
        # The phrase was not found in user_db.
        # Add it as a new phrase, i.e. with user_freq = 1:
        self.add_phrase(input_phrase = input_phrase,
                        phrase = phrase,
                        user_freq = 1,
                        database = 'user_db', commit=True)
        return

    def remove_phrase (self, input_phrase=u'', phrase=u'', database='user_db', commit=True):
        '''
        Remove all rows matching “input_phrase” and “phrase” from database.
        Or, if “input_phrase” is “None”, remove all rows matching “phrase”
        no matter for what input phrase from the database.
        '''
        if not phrase:
            return
        phrase = unicodedata.normalize(
            self._normalization_form_internal, phrase)
        if input_phrase:
            input_phrase = unicodedata.normalize(
                self._normalization_form_internal, input_phrase)
        if input_phrase:
            delete_sqlstr = '''
            DELETE FROM %(database)s.phrases
            WHERE input_phrase = :input_phrase AND phrase = :phrase
            ;''' %{'database': database}
        else:
            delete_sqlstr = '''
            DELETE FROM %(database)s.phrases
            WHERE phrase = :phrase
            ;''' %{'database': database}
        delete_sqlargs = {'input_phrase': input_phrase, 'phrase': phrase}
        self.db.execute(delete_sqlstr, delete_sqlargs)
        if commit:
            self.db.commit()

    def extract_user_phrases(self, database='user_db'):
        '''extract user phrases from database'''
        try:
            db = sqlite3.connect(database)
            phrases = db.execute(
                '''
                SELECT phrase, sum(user_freq)
                FROM phrases
                GROUP BY phrase
                ;''').fetchall()
            db.close()
            return phrases[:]
        except:
            return []
